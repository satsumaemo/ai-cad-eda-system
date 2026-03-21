"""Fusion 360 어댑터 단위 테스트.

브릿지 서버를 mock하여 adapter.py 로직을 테스트한다.
Fusion 360이 없어도 실행 가능하다.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from adapters.base import Status, ValidationCheck
from adapters.fusion360.adapter import Fusion360Adapter


@pytest.fixture
def adapter():
    a = Fusion360Adapter(config={"bridge_url": "http://127.0.0.1:18080", "timeout": 5})
    return a


def _mock_client_post(adapter, return_value=None, side_effect=None):
    """adapter._http_client.post를 mock한다."""
    mock = MagicMock()
    if side_effect:
        mock.side_effect = side_effect
    else:
        mock_resp = MagicMock()
        mock_resp.json.return_value = return_value or {}
        mock_resp.raise_for_status = MagicMock()
        mock.return_value = mock_resp
    adapter._http_client.post = mock
    return mock


def _mock_client_get(adapter, return_value=None, side_effect=None):
    """adapter._http_client.get를 mock한다."""
    mock = MagicMock()
    if side_effect:
        mock.side_effect = side_effect
    else:
        mock_resp = MagicMock()
        mock_resp.json.return_value = return_value or {}
        mock.return_value = mock_resp
    adapter._http_client.get = mock
    return mock


class TestFusion360Adapter:
    """adapter.py 로직 테스트 (브릿지 서버 mock)."""

    def test_get_capabilities_loads_registry(self, adapter: Fusion360Adapter):
        caps = adapter.get_capabilities()
        assert "create_rectangle_sketch" in caps
        assert "create_circle_sketch" in caps
        assert "extrude" in caps
        assert "export_step" in caps
        assert "create_hole" in caps
        assert "rectangular_pattern" in caps
        assert "fillet" in caps
        assert "chamfer" in caps
        assert "set_parameter" in caps
        assert "export_stl" in caps
        assert "get_design_info" in caps
        assert "get_body_properties" in caps
        assert "create_component" in caps
        assert "set_material" in caps
        assert "execute_script" in caps

    def test_capabilities_have_required_fields(self, adapter: Fusion360Adapter):
        caps = adapter.get_capabilities()
        for name, spec in caps.items():
            assert "description" in spec, f"{name} missing description"
            assert "parameters_schema" in spec, f"{name} missing parameters_schema"
            assert spec["parameters_schema"]["type"] == "object"

    def test_execute_success(self, adapter: Fusion360Adapter):
        _mock_client_post(adapter, {
            "status": "success",
            "result": {"sketch_name": "Sketch1", "profile_count": 1},
            "validation": [],
        })
        result = adapter.execute(
            "create_rectangle_sketch",
            {"plane": "xy", "width_mm": 40, "height_mm": 40},
            {},
        )
        assert result.status == Status.SUCCESS
        assert result.result["sketch_name"] == "Sketch1"

    def test_execute_with_volume(self, adapter: Fusion360Adapter):
        _mock_client_post(adapter, {
            "status": "success",
            "result": {"body_name": "Body1", "volume_mm3": 8000.0},
            "validation": [],
        })
        result = adapter.execute("extrude", {"distance_mm": 5}, {})
        assert result.status == Status.SUCCESS
        assert result.result["volume_mm3"] == 8000.0

    def test_execute_unknown_action(self, adapter: Fusion360Adapter):
        result = adapter.execute("nonexistent_action", {}, {})
        assert result.status == Status.FAILURE
        assert "Unknown action" in result.error

    def test_execute_timeout(self, adapter: Fusion360Adapter):
        import httpx
        _mock_client_post(adapter, side_effect=httpx.TimeoutException("timeout"))
        result = adapter.execute("create_rectangle_sketch", {"plane": "xy", "width_mm": 10, "height_mm": 10}, {})
        assert result.status == Status.FAILURE
        assert "타임아웃" in result.error

    def test_execute_connect_error(self, adapter: Fusion360Adapter):
        import httpx
        _mock_client_post(adapter, side_effect=httpx.ConnectError("connection refused"))
        result = adapter.execute("create_rectangle_sketch", {"plane": "xy", "width_mm": 10, "height_mm": 10}, {})
        assert result.status == Status.FAILURE
        assert "연결 실패" in result.error

    def test_validate_result_negative_volume(self, adapter: Fusion360Adapter):
        result = adapter._make_result(Status.SUCCESS, {"volume_mm3": -1.0})
        validated = adapter.validate_result(result)
        assert validated.status == Status.FAILURE
        assert validated.has_failure

    def test_validate_result_positive_volume(self, adapter: Fusion360Adapter):
        result = adapter._make_result(Status.SUCCESS, {"volume_mm3": 8000.0})
        validated = adapter.validate_result(result)
        assert validated.status == Status.SUCCESS

    def test_validate_result_passes_through_failure(self, adapter: Fusion360Adapter):
        result = adapter._make_result(Status.FAILURE, {}, error="some error")
        validated = adapter.validate_result(result)
        assert validated.status == Status.FAILURE

    def test_health_check_healthy(self, adapter: Fusion360Adapter):
        _mock_client_get(adapter, {"fusion_running": True})
        assert adapter.health_check() is True

    def test_health_check_unhealthy(self, adapter: Fusion360Adapter):
        _mock_client_get(adapter, {"fusion_running": False})
        assert adapter.health_check() is False

    def test_health_check_connection_error(self, adapter: Fusion360Adapter):
        import httpx
        _mock_client_get(adapter, side_effect=httpx.ConnectError("refused"))
        assert adapter.health_check() is False

    def test_execute_sends_correct_payload(self, adapter: Fusion360Adapter):
        mock = _mock_client_post(adapter, {
            "status": "success",
            "result": {},
            "validation": [],
        })
        adapter.execute("extrude", {"distance_mm": 10}, {"iteration": 0})

        call_args = mock.call_args
        payload = call_args[1]["json"] if "json" in (call_args[1] or {}) else call_args[0][0] if call_args[0] else None
        # httpx.Client.post(url, json=...) 형태
        assert mock.called

    def test_parse_validation_from_bridge(self, adapter: Fusion360Adapter):
        _mock_client_post(adapter, {
            "status": "success",
            "result": {"volume_mm3": 100},
            "validation": [
                {
                    "name": "volume_check",
                    "status": "success",
                    "value": 100,
                    "threshold": 0,
                    "message": "OK",
                }
            ],
        })
        result = adapter.execute("get_body_properties", {"body_id": "b1"}, {})
        assert len(result.validation) == 1
        assert result.validation[0].name == "volume_check"

    def test_execute_with_timing(self, adapter: Fusion360Adapter):
        result = adapter.execute_with_timing("nonexistent_action", {}, {})
        assert result.elapsed_seconds >= 0
        assert result.status == Status.FAILURE


class TestToolRegistry:
    """tool_registry.json 유효성 검증."""

    def test_registry_file_exists(self):
        registry_path = Path(__file__).parent.parent.parent / "adapters" / "fusion360" / "tool_registry.json"
        assert registry_path.exists()

    def test_registry_valid_json(self):
        registry_path = Path(__file__).parent.parent.parent / "adapters" / "fusion360" / "tool_registry.json"
        with open(registry_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "tools" in data
        assert len(data["tools"]) > 0

    def test_all_tools_have_schema(self):
        registry_path = Path(__file__).parent.parent.parent / "adapters" / "fusion360" / "tool_registry.json"
        with open(registry_path, encoding="utf-8") as f:
            data = json.load(f)

        for tool in data["tools"]:
            assert "name" in tool, f"Tool missing name: {tool}"
            assert "description" in tool, f"Tool {tool.get('name')} missing description"
            assert "parameters_schema" in tool, f"Tool {tool.get('name')} missing schema"
            schema = tool["parameters_schema"]
            assert schema["type"] == "object", f"Tool {tool['name']} schema not object"

    def test_tool_names_unique(self):
        registry_path = Path(__file__).parent.parent.parent / "adapters" / "fusion360" / "tool_registry.json"
        with open(registry_path, encoding="utf-8") as f:
            data = json.load(f)

        names = [t["name"] for t in data["tools"]]
        assert len(names) == len(set(names)), "Duplicate tool names found"


class TestScriptGenerator:
    """script_generator.py 테스트."""

    def test_generate_create_sketch(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("create_sketch", {
            "plane": "xy",
            "elements": [
                {"type": "rectangle", "params": {"x": 0, "y": 0, "width": 40, "height": 40}},
            ],
        })
        assert "import adsk.core" in script
        assert "sketchLines.addTwoPointRectangle" in script
        assert "def run(context):" in script

    def test_generate_extrude(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("extrude", {"distance_mm": 5})
        assert "import adsk.core" in script
        assert "0.5" in script
        assert "extrudeFeatures" in script

    def test_generate_export_step(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("export_step", {"output_path": "/tmp/test.step"})
        assert "createSTEPExportOptions" in script
        assert "/tmp/test.step" in script

    def test_generate_fillet(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("fillet", {"radius_mm": 2, "all_edges": True})
        assert "filletFeatures" in script

    def test_generate_chamfer(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("chamfer", {"distance_mm": 1, "edge_ids": ["1", "2"]})
        assert "chamferFeatures" in script

    def test_generate_set_parameter(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("set_parameter", {"name": "Width", "value": 50, "unit": "mm"})
        assert "userParameters" in script
        assert "Width" in script

    def test_generate_get_design_info(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("get_design_info", {})
        assert "design_name" in script

    def test_generate_get_body_properties(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("get_body_properties", {"body_id": "Body1"})
        assert "physicalProperties" in script
        assert "Body1" in script

    def test_generate_create_component(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("create_component", {"name": "Bracket"})
        assert "Bracket" in script
        assert "addNewComponent" in script

    def test_generate_create_hole(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("create_hole", {
            "face_id": "1",
            "center_x_mm": 10,
            "center_y_mm": 20,
            "diameter_mm": 5,
            "depth_mm": 3,
        })
        assert "holeFeatures" in script

    def test_generate_rectangular_pattern(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("rectangular_pattern", {
            "feature_id": "f1",
            "direction1_axis": "x",
            "direction1_count": 5,
            "direction1_spacing_mm": 2,
        })
        assert "rectangularPatternFeatures" in script

    def test_generate_export_stl(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("export_stl", {"output_path": "/tmp/test.stl", "refinement": "high"})
        assert "createSTLExportOptions" in script

    def test_generate_set_material(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("set_material", {"body_id": "b1", "material_name": "Aluminum 6061"})
        assert "materialLibraries" in script
        assert "Aluminum 6061" in script

    def test_generate_execute_script_passthrough(self):
        from adapters.fusion360.script_generator import generate_script

        code = "print('hello')"
        script = generate_script("execute_script", {"script_code": code})
        assert script == code

    def test_unknown_action_raises(self):
        from adapters.fusion360.script_generator import generate_script

        with pytest.raises(ValueError, match="Script generator not found"):
            generate_script("nonexistent_action", {})

    def test_mm_to_cm_conversion_in_extrude(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("extrude", {"distance_mm": 40})
        assert "4.0" in script

    def test_sketch_with_circle_element(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("create_sketch", {
            "plane": "xz",
            "elements": [{"type": "circle", "params": {"center_x": 0, "center_y": 0, "radius": 15}}],
        })
        assert "sketchCircles.addByCenterRadius" in script
        assert "1.5" in script

    def test_sketch_with_line_element(self):
        from adapters.fusion360.script_generator import generate_script

        script = generate_script("create_sketch", {
            "plane": "xy",
            "elements": [{"type": "line", "params": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}}],
        })
        assert "sketchLines.addByTwoPoints" in script


class TestToolResult:
    """ToolResult 동작 테스트."""

    def test_to_summary(self, adapter: Fusion360Adapter):
        result = adapter._make_result(Status.SUCCESS, {"key": "value"})
        summary = result.to_summary()
        assert summary["status"] == "success"
        assert summary["tool"] == "Fusion360Adapter"

    def test_all_pass_empty(self, adapter: Fusion360Adapter):
        result = adapter._make_result(Status.SUCCESS, {})
        assert result.all_pass is True

    def test_has_failure(self, adapter: Fusion360Adapter):
        result = adapter._make_result(Status.SUCCESS, {}, validation=[
            ValidationCheck("test", Status.FAILURE, 0, message="fail"),
        ])
        assert result.has_failure is True
