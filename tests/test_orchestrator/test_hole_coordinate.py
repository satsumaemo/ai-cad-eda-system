"""홀 좌표 계산 및 바운딩박스 컨텍스트 테스트."""

import json
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.llm_client import LLMResponse, ToolCall


@pytest.fixture
def orch():
    """Orchestrator를 가볍게 생성 (어댑터 로딩 스킵)."""
    with patch("orchestrator.core.load_config", return_value={
        "llm": {"provider": "gemini", "api_key": "test"},
        "adapters": {},
    }), patch("orchestrator.core.Orchestrator._load_adapters"), \
         patch("orchestrator.core.Orchestrator._register_tools"):
        from orchestrator.core import Orchestrator
        mock_llm = MagicMock()
        o = Orchestrator(llm_client=mock_llm)
        o.tools = [{"name": "dummy"}]
        return o


class TestFusionContextBoundingBox:
    """_get_fusion_context가 바운딩박스 min/max/중앙을 포함하는지."""

    def test_bbox_includes_min_max_and_center(self, orch):
        """60x40x25mm 박스의 바운딩박스 중앙 XY가 표시된다."""
        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_result.result = {
            "design_name": "TestDesign",
            "bodies": [{"name": "Body1"}],
            "bounding_box": {
                "min": [0.0, 0.0, 0.0],
                "max": [60.0, 40.0, 25.0],
            },
            "mass_properties": [],
            "parameters": [],
        }
        mock_adapter.execute.return_value = mock_result
        orch.adapters["fusion360"] = mock_adapter

        ctx = orch._get_fusion_context()

        assert "60.0 x 40.0 x 25.0 mm" in ctx
        assert "min=(0.0, 0.0, 0.0)" in ctx
        assert "max=(60.0, 40.0, 25.0)" in ctx
        assert "중앙 XY=(30.0, 20.0)mm" in ctx

    def test_bbox_offset_origin(self, orch):
        """원점이 (0,0,0)이 아닌 경우에도 중앙이 올바르게 계산된다."""
        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_result.result = {
            "design_name": "OffsetDesign",
            "bodies": [{"name": "Body1"}],
            "bounding_box": {
                "min": [-30.0, -20.0, 0.0],
                "max": [30.0, 20.0, 25.0],
            },
            "mass_properties": [],
            "parameters": [],
        }
        mock_adapter.execute.return_value = mock_result
        orch.adapters["fusion360"] = mock_adapter

        ctx = orch._get_fusion_context()

        assert "중앙 XY=(0.0, 0.0)mm" in ctx


class TestHoleCenterCoordinate:
    """'윗면 중앙에 홀' 시나리오 — 좌표 (0, 0)으로 호출되는지."""

    def test_top_center_hole_uses_zero_offset(self, orch):
        """60x40x25mm 박스 생성 후 '윗면 중앙 M5 관통 홀' →
        create_hole(face_id='top', center_x_mm=0, center_y_mm=0)."""
        call_count = 0
        captured_hole_params = {}

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(
                        id="c1", name="fusion360__create_hole",
                        input={
                            "face_id": "top",
                            "center_x_mm": 0,
                            "center_y_mm": 0,
                            "diameter_mm": 5,
                            "through_all": True,
                        },
                    )],
                )
            else:
                return LLMResponse(text="홀 완료", stop_reason="end_turn")

        def fake_route(tool_name, tool_input):
            if "create_hole" in tool_name:
                captured_hole_params.update(tool_input)
            return {
                "status": "success",
                "result": {
                    "hole_name": "Extrude2",
                    "diameter_mm": 5,
                    "through_all": True,
                    "face_centroid_mm": [30.0, 20.0, 25.0],
                },
            }

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = fake_route

        orch.conversation = [
            {"role": "user", "content": "윗면 중앙에 M5 관통 홀 뚫어줘"},
        ]
        result = orch._conversation_loop()

        # create_hole이 호출됨
        assert captured_hole_params["face_id"] == "top"
        # 면 중심 기준 오프셋 = 0,0 (정중앙)
        assert captured_hole_params["center_x_mm"] == 0
        assert captured_hole_params["center_y_mm"] == 0
        assert captured_hole_params["diameter_mm"] == 5
        assert captured_hole_params["through_all"] is True
        assert "✅" in result


class TestSystemPromptHoleGuidance:
    """시스템 프롬프트에 홀 좌표 계산 안내가 포함되는지."""

    def test_system_prompt_contains_coordinate_guidance(self):
        from orchestrator.system_prompt import SYSTEM_PROMPT

        assert "위치 기반 도구 좌표 계산" in SYSTEM_PROMPT
        assert "center_x_mm=0, center_y_mm=0" in SYSTEM_PROMPT
        assert "면 중심 기준 오프셋" in SYSTEM_PROMPT

    def test_system_prompt_contains_top_face_example(self):
        from orchestrator.system_prompt import SYSTEM_PROMPT

        assert 'face_id="top"' in SYSTEM_PROMPT
        assert "윗면 중앙에 홀" in SYSTEM_PROMPT


class TestSystemPromptPolygonGuidance:
    """시스템 프롬프트에 정다각형 → execute_script 안내가 포함되는지."""

    def test_polygon_listed_as_execute_script(self):
        from orchestrator.system_prompt import SYSTEM_PROMPT

        # 복합 형상 목록에 정다각형 포함
        assert "정다각형 기둥" in SYSTEM_PROMPT
        assert "폴리곤" in SYSTEM_PROMPT

    def test_polygon_example_exists(self):
        from orchestrator.system_prompt import SYSTEM_PROMPT

        # 정다각형 코드 예제 존재
        assert "정다각형 기둥" in SYSTEM_PROMPT
        assert "꼭짓점 수" in SYSTEM_PROMPT
        assert "math.cos(angle)" in SYSTEM_PROMPT
        assert "addByTwoPoints" in SYSTEM_PROMPT

    def test_hexagon_in_examples(self):
        from orchestrator.system_prompt import SYSTEM_PROMPT

        assert "정육각형 기둥" in SYSTEM_PROMPT

    def test_polygon_routed_to_execute_script(self, orch):
        """'정육각형 기둥' 요청 시 execute_script로 호출되는지."""
        call_count = 0
        captured_tool = None

        def fake_chat(**kwargs):
            nonlocal call_count, captured_tool
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(
                        id="c1", name="fusion360__execute_script",
                        input={"code": (
                            "import adsk.core, adsk.fusion, traceback, json, math\n"
                            "def run(context):\n"
                            "    try:\n"
                            "        app = adsk.core.Application.get()\n"
                            "        design = adsk.fusion.Design.cast(app.activeProduct)\n"
                            "        root = design.rootComponent\n"
                            "        r = 1.5\n"
                            "        h = 2.0\n"
                            "        n = 6\n"
                            "        sketch = root.sketches.add(root.xYConstructionPlane)\n"
                            "        lines = sketch.sketchCurves.sketchLines\n"
                            "        points = []\n"
                            "        for i in range(n):\n"
                            "            angle = math.pi * 2 * i / n\n"
                            "            x = r * math.cos(angle)\n"
                            "            y = r * math.sin(angle)\n"
                            "            points.append(adsk.core.Point3D.create(x, y, 0))\n"
                            "        for i in range(n):\n"
                            "            lines.addByTwoPoints(points[i], points[(i + 1) % n])\n"
                            "        prof = sketch.profiles.item(0)\n"
                            "        ext = root.features.extrudeFeatures\n"
                            "        inp = ext.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)\n"
                            "        inp.setDistanceExtent(False, adsk.core.ValueInput.createByReal(h))\n"
                            "        ext.add(inp)\n"
                            '        print(json.dumps({"status": "success", "result": {"description": "정6각형 기둥 완료"}}))\n'
                            "    except:\n"
                            '        print(json.dumps({"status": "failure", "error": traceback.format_exc(), "result": {}}))\n'
                        )},
                    )],
                )
            else:
                return LLMResponse(text="완료", stop_reason="end_turn")

        def fake_route(tool_name, tool_input):
            nonlocal captured_tool
            captured_tool = tool_name
            return {
                "status": "success",
                "result": {"description": "정6각형 기둥 완료"},
            }

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = fake_route

        orch.conversation = [
            {"role": "user", "content": "정육각형 기둥 외접원 지름 30mm 높이 20mm 만들어줘"},
        ]
        result = orch._conversation_loop()

        # execute_script로 호출됨 (create_circle_sketch가 아님)
        assert captured_tool == "fusion360__execute_script"
        assert "✅" in result
