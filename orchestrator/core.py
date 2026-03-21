"""오케스트레이터 메인 루프 — LLM API를 호출하고 tool_use 응답을 어댑터로 라우팅한다.

LLM 프로바이더(Gemini/Claude)는 LLMClient 인터페이스로 추상화되어 있으며,
config의 llm.provider 설정으로 교체 가능하다.

승인 후 실행:
    단순 형상(박스, 홀, 필렛) → plan_parser direct execution
    복합 형상(L자, 방열판, 인클로저 등) → conversation loop (AI가 도구 조합 판단)
"""

import importlib
import json
import logging
from typing import Any

from adapters.base import BaseAdapter, Status, ToolResult
from config.loader import load_config
from orchestrator.llm_client import LLMClient, ToolResultMessage, create_llm_client
from orchestrator.plan_parser import (
    ParsedStep,
    enrich_step_params,
    infer_plan_from_conversation,
    parse_plan_from_conversation,
)
from orchestrator.planner import Planner
from orchestrator.result_analyzer import ResultAnalyzer
from orchestrator.system_prompt import SYSTEM_PROMPT
from pipeline.snapshot import SnapshotManager
from pipeline.state import PipelineState

logger = logging.getLogger(__name__)

# 어댑터별 컨텍스트 키워드
_ADAPTER_KEYWORDS: dict[str, list[str]] = {
    "fusion360": [
        "fusion", "모델링", "스케치", "돌출", "extrude", "필렛", "챔퍼",
        "cad", "3d", "바디", "body", "컴포넌트", "step", "stl",
        "형상", "박스", "홀", "hole", "패턴", "재질", "material",
    ],
}


def _extract_conversation_text(conversation: list[dict[str, Any]], last_n: int = 6) -> str:
    """최근 대화 메시지에서 텍스트를 추출한다."""
    texts: list[str] = []
    for msg in conversation[-last_n:]:
        content = msg.get("content", "")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for p in content:
                if isinstance(p, dict):
                    texts.append(p.get("text", ""))
        parts = msg.get("parts", [])
        if isinstance(parts, list):
            for p in parts:
                if isinstance(p, dict):
                    texts.append(p.get("text", ""))
    return " ".join(texts).lower()




class Orchestrator:
    """LLM 기반 오케스트레이터 메인 클래스.

    config["llm"]["provider"]로 LLM 프로바이더를 선택한다:
        - "gemini" (기본): Google Gemini API (google-genai SDK)
        - "claude": Anthropic Claude API (anthropic SDK)
    """

    def __init__(self, config: dict | None = None, llm_client: LLMClient | None = None) -> None:
        self.config = config if config is not None else load_config()

        self.adapters: dict[str, BaseAdapter] = {}
        self.llm = llm_client if llm_client is not None else create_llm_client(self.config)
        self.state = PipelineState()
        self.planner = Planner()
        self.analyzer = ResultAnalyzer()
        self.snapshot_mgr = SnapshotManager()
        self.conversation: list[dict[str, Any]] = []
        self.tools: list[dict] = []

        self._load_adapters()
        self._register_tools()

    def _get_fusion_context(self) -> str:
        """Fusion 360의 현재 모델 정보를 조회하여 텍스트로 반환한다."""
        fusion = self.adapters.get("fusion360")
        if fusion is None:
            return ""

        try:
            result = fusion.execute("get_design_info", {
                "include_parameters": True,
                "include_bodies": True,
                "include_bounding_box": True,
                "include_mass_properties": True,
            }, {})

            if result.status.value != "success":
                return "[현재 Fusion 모델] 조회 실패"

            data = result.result
            lines: list[str] = ["[현재 Fusion 모델]"]

            # 바디 정보
            bodies = data.get("bodies", [])
            if not bodies:
                lines.append("바디 없음 (빈 디자인)")
                return "\n".join(lines)

            lines.append(f"디자인: {data.get('design_name', '?')}")
            lines.append(f"바디: {len(bodies)}개")

            # 바운딩박스
            bbox = data.get("bounding_box")
            if bbox:
                mn, mx = bbox.get("min", [0, 0, 0]), bbox.get("max", [0, 0, 0])
                sx = abs(mx[0] - mn[0])
                sy = abs(mx[1] - mn[1])
                sz = abs(mx[2] - mn[2])
                lines.append(f"바운딩박스: {sx:.1f} x {sy:.1f} x {sz:.1f} mm")
                lines.append(f"  min=({mn[0]:.1f}, {mn[1]:.1f}, {mn[2]:.1f}), max=({mx[0]:.1f}, {mx[1]:.1f}, {mx[2]:.1f})")
                cx = (mn[0] + mx[0]) / 2
                cy = (mn[1] + mx[1]) / 2
                lines.append(f"  중앙 XY=({cx:.1f}, {cy:.1f})mm")

            # 물성
            mass_props = data.get("mass_properties", [])
            for mp in mass_props:
                lines.append(f"  {mp.get('body', '?')}: 부피 {mp.get('volume_mm3', 0):.1f}mm3, 면적 {mp.get('area_mm2', 0):.1f}mm2")

            # 파라미터
            params = data.get("parameters", [])
            if params:
                lines.append("파라미터:")
                for p in params[:10]:
                    lines.append(f"  {p['name']} = {p.get('expression', p.get('value', '?'))}")

            return "\n".join(lines)
        except Exception as e:
            logger.debug("Fusion context query failed (non-fatal): %s", e)
            return ""

    def _load_adapters(self) -> None:
        """config에 등록된 어댑터를 동적으로 로드한다."""
        tools_config = self.config.get("tools", self.config)
        for name, adapter_config in tools_config.get("adapters", {}).items():
            if not adapter_config.get("enabled", False):
                logger.info("Adapter %s is disabled, skipping", name)
                continue
            try:
                module = importlib.import_module(adapter_config["module"])
                cls = getattr(module, adapter_config["class"])
                self.adapters[name] = cls(config=adapter_config)
                logger.info("Loaded adapter: %s", name)
            except Exception as e:
                logger.error("Failed to load adapter %s: %s", name, e)

    def _register_tools(self) -> None:
        """각 어댑터의 capabilities를 LLM tools로 변환한다."""
        self.tools = []
        for name, adapter in self.adapters.items():
            try:
                caps = adapter.get_capabilities()
                for action_name, action_spec in caps.items():
                    self.tools.append({
                        "name": f"{name}__{action_name}",
                        "description": action_spec["description"],
                        "input_schema": action_spec["parameters_schema"],
                    })
            except Exception as e:
                logger.error("Failed to register tools for %s: %s", name, e)

    def _route_tool_call(self, tool_name: str, tool_input: dict) -> dict:
        """tool_use 응답을 해당 어댑터로 라우팅한다."""
        parts = tool_name.split("__", 1)
        if len(parts) != 2:
            return {"error": f"Invalid tool name format: {tool_name}"}

        adapter_name, action = parts
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            return {"error": f"Unknown adapter: {adapter_name}"}

        # execute_script 전처리
        if action == "execute_script" and "script_code" in tool_input:
            tool_input["script_code"] = self._preprocess_script(
                tool_input["script_code"]
            )

        # 스냅샷 저장
        self.snapshot_mgr.save(self.state.to_dict())

        context = {
            "state": self.state.to_dict(),
            "iteration": self.state.current_iteration,
        }

        result = adapter.execute_with_timing(action, tool_input, context)
        result = adapter.validate_result(result)

        # 상태 업데이트
        self.state.record_step(adapter_name, action, result.to_summary())

        return result.to_summary()

    def _preprocess_script(self, code: str) -> str:
        """LLM이 생성한 스크립트를 전처리한다."""
        import re

        # 1. 마크다운 코드블록 제거
        if "```" in code:
            m = re.search(r"```(?:python)?\s*\n(.*?)```", code, re.DOTALL)
            if m:
                code = m.group(1)

        code = code.strip()

        # 2. 알려진 API 실수 교정
        code = self._sanitize_fusion_script(code)

        # 3. return {…} → print(json.dumps(…)) 변환
        #    Fusion 스크립트의 run()에서 return은 값 반환이 안 되므로
        #    print로 stdout에 출력해야 FusionBridge가 캡처할 수 있다.
        code = self._convert_return_to_print(code)

        # 4. json import 보장 (print(json.dumps(…)) 사용하므로)
        if "json.dumps" in code:
            # "import json" 또는 "import ..., json" 패턴 탐색
            import re as _re
            has_json_import = bool(_re.search(
                r"^\s*import\s+.*\bjson\b", code, _re.MULTILINE
            ))
            if not has_json_import:
                code = "import json\n" + code

        # 5. def run() 없으면 감싸기
        if "def run(" not in code:
            indent = "    "
            lines = code.split("\n")
            imports: list[str] = []
            body: list[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    imports.append(line)
                else:
                    body.append(line)
            wrapped = "\n".join(imports) + "\n\n"
            wrapped += "def run(context):\n"
            wrapped += indent + "try:\n"
            for line in body:
                if line.strip():
                    wrapped += indent + indent + line + "\n"
                else:
                    wrapped += "\n"
            wrapped += (
                indent + "except Exception:\n"
                + indent + indent + "import traceback\n"
                + indent + indent + "print(json.dumps({'status': 'failure', 'error': traceback.format_exc(), 'result': {}}))\n"
            )
            code = wrapped
            logger.info("[preprocess_script] def run() 래핑 적용")

        return code

    @staticmethod
    def _convert_return_to_print(code: str) -> str:
        """return {…} / return {"status":…} → print(json.dumps(…)) 변환.

        Fusion 스크립트에서 return으로 값을 반환할 수 없으므로
        print(json.dumps(…))로 stdout에 출력하는 형태로 변환한다.
        """
        import re
        lines = code.split("\n")
        result_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            # "return {...}" 또는 "return {" (여러 줄 dict의 시작)
            m = re.match(r"^(\s*)return\s+(\{.*\})\s*$", line)
            if m:
                indent = m.group(1)
                dict_expr = m.group(2)
                result_lines.append(f"{indent}print(json.dumps({dict_expr}))")
                result_lines.append(f"{indent}return")
                continue

            # "return {" (dict 시작, 아직 닫히지 않음) — 그대로 유지 (복잡한 케이스)
            result_lines.append(line)

        return "\n".join(result_lines)

    def run(self, user_message: str) -> str:
        """사용자 메시지를 처리하고 최종 응답을 반환한다."""
        logger.info("[run] 메시지 수신: %s", user_message[:100])

        msg_lower = user_message.lower()

        # ── 미지원 기능 감지 (시뮬레이션/PCB/해석) ──
        _UNSUPPORTED_KEYWORDS = [
            "구조 해석", "구조해석", "응력 해석", "응력해석", "structural analysis",
            "열 해석", "열해석", "thermal analysis", "온도 해석",
            "시뮬레이션", "simulation", "fea", "fem", "cfd",
            "유동 해석", "유체 해석", "전자기 해석",
            "pcb", "기판", "보드 생성", "보드 만들", "drc", "거버", "gerber", "bom",
            "최적화", "optimize",
        ]
        if any(kw in msg_lower for kw in _UNSUPPORTED_KEYWORDS):
            reply = (
                "이 기능은 현재 지원되지 않습니다.\n\n"
                "현재 지원 기능: 3D 모델링, 형상 수정(필렛/챔퍼/홀), 재질 할당, STEP/STL 내보내기\n"
                "시뮬레이션(구조/열/CFD/전자기), PCB 설계, 최적화 기능은 향후 업데이트에서 제공 예정입니다."
            )
            self.conversation.append({"role": "user", "content": user_message})
            self.conversation.append({"role": "assistant", "content": reply})
            return reply

        # ── "계속 시도해줘" → 카운터 리셋 후 루프 재진입 ──
        if msg_lower in ("계속 시도해줘", "계속 시도"):
            logger.info("[run] 사용자 요청: 계속 시도 — 루프 재진입")
            self.conversation.append({"role": "user", "content": user_message})
            return self._conversation_loop()

        # ── "이 작업은 건너뛰고 다음 진행해줘" → 현재 작업 건너뛰기 ──
        if "건너뛰" in msg_lower and "다음" in msg_lower:
            logger.info("[run] 사용자 요청: 건너뛰기")
            self.conversation.append({"role": "user", "content": user_message})
            self.conversation.append({"role": "assistant", "content":
                "이 작업을 건너뛰었습니다. 다음 요청을 입력해주세요."})
            return "이 작업을 건너뛰었습니다. 다음 요청을 입력해주세요."

        # ── "작업 중단해줘" → 중단 ──
        if "작업 중단" in msg_lower or "중단해줘" in msg_lower:
            logger.info("[run] 사용자 요청: 작업 중단")
            self.conversation.append({"role": "user", "content": user_message})
            self.conversation.append({"role": "assistant", "content":
                "작업을 중단했습니다."})
            return "작업을 중단했습니다."

        self.conversation.append({"role": "user", "content": user_message})
        logger.info("[run] 대화 이력 %d턴 — Conversation Loop 진입", len(self.conversation))
        return self._conversation_loop()


    @staticmethod
    def _sanitize_fusion_script(script: str) -> str:
        """LLM이 생성한 Fusion Python 스크립트의 알려진 오류를 교정한다."""
        import re

        # 1. FeatureDirections — 존재하지 않는 API
        #    setDistanceExtent(False, distance) 형태로 이미 방향이 결정됨
        script = re.sub(
            r",\s*adsk\.fusion\.FeatureDirections\.\w+", "", script
        )
        script = re.sub(
            r"adsk\.fusion\.FeatureDirections\.\w+,?\s*", "", script
        )

        # 2. ExtentDirections — 역시 존재하지 않는 API
        script = re.sub(
            r",\s*adsk\.fusion\.ExtentDirections\.\w+", "", script
        )

        # 3. FeatureOperations enum: 모든 형태를 getattr() 호출로 변환
        #    이렇게 하면 FusionBridge의 구버전 sanitizer가 .replace로 깨뜨리지 못함
        _OP_CANONICAL = {
            "NewBody": "NewBodyFeatureOperation",
            "NewBodyFeatureOperation": "NewBodyFeatureOperation",
            "Join": "JoinFeatureOperation",
            "JoinFeatureOperation": "JoinFeatureOperation",
            "Cut": "CutFeatureOperation",
            "CutFeatureOperation": "CutFeatureOperation",
            "Intersect": "IntersectFeatureOperation",
            "IntersectFeatureOperation": "IntersectFeatureOperation",
        }
        def _replace_op(m: re.Match) -> str:
            name = m.group(1)
            canonical = _OP_CANONICAL.get(name, name)
            return f'getattr(adsk.fusion.FeatureOperations, "{canonical}")'

        script = re.sub(
            r"adsk\.fusion\.FeatureOperations\.(\w+)",
            _replace_op,
            script,
        )

        # 4. setDistanceExtent 인자 수정:
        #    잘못: setDistanceExtent(True, distance, direction)  (3인자)
        #    올바름: setDistanceExtent(False, distance)  (2인자)
        script = re.sub(
            r"setDistanceExtent\(\s*(?:True|False)\s*,\s*([^,)]+)\s*,\s*[^)]+\)",
            r"setDistanceExtent(False, \1)",
            script,
        )

        # 5. addTwoPointRectangle 좌표가 mm 단위로 되어있으면 경고만 (자동 교정 어려움)

        # 6. profiles.item(profiles.count - 1) 안전화
        #    잘못: sketch.profiles[0]  (인덱싱 불가)
        script = script.replace("sketch.profiles[0]", "sketch.profiles.item(0)")
        script = re.sub(
            r"\.profiles\[(\d+)\]",
            r".profiles.item(\1)",
            script,
        )

        # 7. shellFeatures.createInput 3인자 → 2인자
        #    잘못: createInput(faces, False, adsk.core.ValueInput.createByReal(0.2))
        #    올바름: createInput(faces, False)
        script = re.sub(
            r"(shellFeatures\.createInput\(\s*[^,]+,\s*(?:True|False))\s*,\s*.+?\)\s*\)",
            r"\1)",
            script,
        )

        # 8. SurfaceTypes enum: XXXSurface → XXXSurfaceType
        #    잘못: SurfaceTypes.PlaneSurface
        #    올바름: SurfaceTypes.PlaneSurfaceType
        script = re.sub(
            r"SurfaceTypes\.(\w+?)Surface\b(?!Type)",
            r"SurfaceTypes.\1SurfaceType",
            script,
        )

        # 9. 컬렉션 인덱싱 교정: .faces[i] → .faces.item(i), .edges[i] → .edges.item(i)
        for attr in ("faces", "edges", "bodies", "bRepBodies", "sketches",
                      "occurrences", "features"):
            script = re.sub(
                rf"\.{attr}\[([^\]]+)\]",
                rf".{attr}.item(\1)",
                script,
            )

        return script

    # ─── plan_parser 폴백 실행 ───

    def _try_plan_parser_fallback(self, already_done: list[dict] | None = None) -> str | None:
        """대화 이력에서 plan_parser로 계획을 추출하여 직접 실행한다.

        Gemini가 도구 체이닝에 실패했을 때 (빈 응답, 중간 텍스트 반복 등)
        이전 계획 텍스트를 파싱하여 남은 단계를 순차 실행한다.

        Args:
            already_done: 이미 실행된 도구 목록 (중복 실행 방지)

        Returns:
            실행 보고 문자열. 파싱 실패 시 None.
        """
        # 원래 대화만 추출 (시스템 주입 메시지, tool result 짧은 메시지 제외)
        _SYSTEM_INJECTED = [
            "작업을 완료해주세요", "계속 진행하세요", "설명만 하지 말고",
            "도구를 호출하여 실행", "올바른 형식으로 다시",
            "나머지 단계도", "선행 작업이 필요하면",
        ]
        original_msgs = []
        for m in self.conversation:
            content = m.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            # 시스템 주입 메시지 제외
            if any(inj in content for inj in _SYSTEM_INJECTED):
                continue
            # tool result 등 짧은 시스템 메시지 제외 (10자 이하)
            if len(content.strip()) <= 10 and m.get("role") in ("assistant", "user"):
                # 단, 승인 메시지("네", "그래", "승인" 등)는 유지
                if m.get("role") == "user":
                    original_msgs.append(m)
                continue
            original_msgs.append(m)

        # 1순위: infer_plan (원래 대화에서 치수 추출)
        steps = infer_plan_from_conversation(original_msgs)
        if not steps:
            steps = parse_plan_from_conversation(original_msgs)
        if not steps:
            logger.warning("[plan_fallback] 계획 파싱 실패")
            return None

        # 이미 성공한 도구 필터링
        done_keys: set[str] = set()
        if already_done:
            for a in already_done:
                if a.get("status") == "success":
                    done_keys.add(a.get("tool", ""))

        remaining = [s for s in steps if s.tool_name not in done_keys]
        if not remaining:
            # 모든 단계가 이미 성공 → 재실행 없이 성공 결과 반환
            logger.info("[plan_fallback] 모든 단계 이미 완료 (%d개) — 성공 반환", len(steps))
            report = "\n".join(f"✅ {s.description}" for s in steps)
            self.conversation.append({"role": "assistant", "content": report})
            return report

        logger.info("[plan_fallback] %d단계 실행 (이미 완료: %d)",
                    len(remaining), len(done_keys))

        return self._execute_parsed_steps(remaining)

    def _execute_parsed_steps(self, steps: list[ParsedStep]) -> str:
        """ParsedStep 리스트를 순차 실행하고 요약을 반환한다."""
        report_lines: list[str] = []
        previous_results: list[dict[str, Any]] = []

        for step in steps:
            step = enrich_step_params(step, previous_results)
            logger.info("[exec_steps] %d: %s(%s)",
                        step.order, step.tool_name,
                        json.dumps(step.parameters, ensure_ascii=False)[:200])

            try:
                result = self._route_tool_call(step.tool_name, step.parameters)
            except Exception as e:
                report_lines.append(f"❌ {step.description} 실패: {e}")
                logger.error("[exec_steps] %d 실패: %s", step.order, e)
                break

            previous_results.append(result)
            status = result.get("status", "unknown")

            if status == "failure":
                error = result.get("error", "알 수 없는 오류")
                # 필렛/챔퍼는 비치명적 → 건너뛰기
                if step.action in ("fillet", "chamfer") and len(steps) > 1:
                    report_lines.append(f"⚠️ {step.description} 건너뜀: {error[:100]}")
                    continue
                report_lines.append(f"❌ {step.description} 실패: {error[:200]}")
                break
            else:
                report_lines.append(f"✅ {step.description}")

        report = "\n".join(report_lines)
        self.conversation.append({"role": "assistant", "content": report})
        return report

    # ─── LLM 대화 루프 (LLM 독립적 견고한 구현) ───

    _MAX_TOOL_ROUNDS = 20
    _MAX_SAME_TOOL_REPEAT = 3
    _MAX_EMPTY_TEXT_RETRIES = 2
    _MAX_CONTINUATION_NUDGES = 5  # 중간 텍스트 후 "계속 실행" 재요청 최대 횟수

    # 단독 성공 시 루프를 즉시 종료하는 터미널 도구 (후속 호출 불필요)
    _TERMINAL_TOOLS = (
        "set_material", "fillet", "chamfer",
        "export_step", "export_stl",
        "get_design_info", "create_hole",
    )
    _MAX_MALFORMED_RETRIES = 1

    def _conversation_loop(self) -> str:
        """LLM과의 대화 루프를 실행한다.

        LLM 프로바이더에 독립적으로 동작하는 견고한 루프:
        - 최대 tool call 횟수 제한
        - 같은 도구 연속 호출 감지 → 중단
        - MALFORMED_FUNCTION_CALL 재시도
        - 빈 텍스트 재요청
        """
        logger.info("[conv_loop] 시작 (messages=%d, tools=%d)",
                     len(self.conversation), len(self.tools))
        tool_rounds = 0
        empty_retries = 0
        continuation_nudges = 0  # 중간 텍스트 후 "계속 실행" 재요청 횟수 (별도 카운터)
        malformed_retries = 0
        recent_tool_keys: list[str] = []
        completed_actions: list[dict] = []
        # 성공한 도구 호출 캐시: "name:params_json" → result_json (중복 실행 방지)
        success_cache: dict[str, str] = {}
        # 연속 실패 에러 추적: 같은 에러 3회 반복 시 중단
        recent_errors: list[str] = []

        # Fusion 모델 정보를 시스템 프롬프트에 포함 (루프 시작 시 1회)
        fusion_ctx = self._get_fusion_context()
        system = SYSTEM_PROMPT
        if fusion_ctx:
            system = f"{SYSTEM_PROMPT}\n\n{fusion_ctx}"

        while True:
            try:
                response = self.llm.chat(
                    messages=self.conversation,
                    system=system,
                    tools=self.tools if self.tools else None,
                    tool_config_mode="AUTO",
                )
            except Exception as e:
                logger.exception("[conv_loop] LLM chat() 예외")
                return f"LLM 호출 실패: {e}"

            logger.info(
                "[conv_loop] 응답: stop=%s, tools=%d, text=%d chars",
                response.stop_reason, len(response.tool_calls), len(response.text),
            )

            # ── MALFORMED_FUNCTION_CALL: 재시도 후 폴백 ──
            if response.stop_reason == "malformed_tool_call":
                malformed_retries += 1
                if malformed_retries <= self._MAX_MALFORMED_RETRIES:
                    logger.warning("[conv_loop] MALFORMED (%d/%d) — 재시도",
                                   malformed_retries, self._MAX_MALFORMED_RETRIES)
                    self.conversation.append({"role": "user", "content":
                        "도구 호출 형식이 잘못되었습니다. 올바른 형식으로 다시 호출해주세요."})
                    continue
                logger.warning("[conv_loop] MALFORMED 재시도 초과 — plan_parser 폴백")
                fallback = self._try_plan_parser_fallback(completed_actions)
                if fallback:
                    return fallback
                return "도구 호출에 반복 오류가 발생했습니다. 요청을 더 구체적으로 해주세요."

            # ── 에러 ──
            if response.stop_reason == "error":
                logger.error("[conv_loop] LLM 에러: %s", response.text)
                return response.text or "LLM 에러 (빈 응답)"

            # ── 텍스트 응답 (도구 호출 없음) ──
            if not response.has_tool_calls:
                if not response.text.strip():
                    empty_retries += 1
                    if empty_retries <= self._MAX_EMPTY_TEXT_RETRIES:
                        logger.warning("[conv_loop] 빈 텍스트 (%d/%d) — 재요청",
                                       empty_retries, self._MAX_EMPTY_TEXT_RETRIES)
                        self.conversation.append({"role": "user", "content":
                            "작업을 완료해주세요. 도구를 호출하여 실행하거나, 결과를 텍스트로 알려주세요."})
                        continue
                    # plan_parser 폴백
                    logger.warning("[conv_loop] 빈 텍스트 한도 초과 — plan_parser 폴백")
                    fallback = self._try_plan_parser_fallback(completed_actions)
                    if fallback:
                        return fallback
                    return "(LLM이 응답하지 않습니다. 요청을 다시 시도해주세요.)"

                text = response.text
                logger.info("[conv_loop] 텍스트 응답 (len=%d)", len(text))
                self.conversation.append({"role": "assistant", "content": text})

                # ── 도구 실행 이력이 없으면: 계획 텍스트 등 → 그대로 반환
                if not completed_actions:
                    return text

                # ── 도구 실행 이력이 있을 때: 완료 여부 판단 ──
                all_failed = all(a.get("status") == "failure" for a in completed_actions)

                # 전부 실패 → LLM에게 실행을 요청
                if all_failed and continuation_nudges < self._MAX_CONTINUATION_NUDGES:
                    continuation_nudges += 1
                    logger.warning("[conv_loop] 모든 도구 실패 후 텍스트 — 실행 재요청 (%d/%d)",
                                   continuation_nudges, self._MAX_CONTINUATION_NUDGES)
                    self.conversation.append({"role": "user", "content":
                        "설명만 하지 말고 실제로 도구를 호출하여 실행해주세요. "
                        "선행 작업이 필요하면 그 도구부터 호출하세요."})
                    continue

                # 텍스트가 완료를 나타내는지 판단
                text_lower = text.lower()
                _COMPLETION_SIGNALS = [
                    "완료", "성공적으로", "모든 작업", "모두 완료",
                    "작업이 끝", "마무리", "결과를 보고",
                    "finished", "completed", "successfully",
                ]
                is_completion = any(s in text_lower for s in _COMPLETION_SIGNALS)

                if is_completion:
                    logger.info("[conv_loop] 완료 텍스트 감지 — 종료 (actions=%d)",
                                len(completed_actions))
                    return text

                # 완료 표현 없음 → 중간 텍스트 → "계속 실행하세요" 재요청
                if continuation_nudges < self._MAX_CONTINUATION_NUDGES:
                    continuation_nudges += 1
                    logger.warning("[conv_loop] 중간 텍스트 — 계속 실행 요청 (%d/%d)",
                                   continuation_nudges, self._MAX_CONTINUATION_NUDGES)
                    self.conversation.append({"role": "user", "content":
                        "계속 진행하세요. 나머지 단계도 도구를 호출하여 실행하세요. "
                        "모든 단계가 끝나면 '완료'라고 보고하세요."})
                    continue

                # 재요청 한도 초과 → plan_parser 폴백
                logger.warning("[conv_loop] 재요청 한도 초과 — plan_parser 폴백")
                fallback = self._try_plan_parser_fallback(completed_actions)
                if fallback:
                    return fallback
                return text

            # ── 도구 호출 처리 ──
            continuation_nudges = 0  # 도구 호출 시 재요청 카운터 리셋
            tool_rounds += 1
            logger.info("[conv_loop] tool round %d/%d, calls=%d",
                        tool_rounds, self._MAX_TOOL_ROUNDS, len(response.tool_calls))

            # 최대 횟수 초과 → plan_parser 폴백 후 사용자에게 선택권 부여
            if tool_rounds > self._MAX_TOOL_ROUNDS:
                logger.warning("[conv_loop] 최대 tool round 초과 — plan_parser 폴백")
                fallback = self._try_plan_parser_fallback(completed_actions)
                if fallback:
                    return fallback
                last_errors = self._collect_recent_errors(completed_actions)
                action_summary = self._summarize_completed_actions(completed_actions)
                msg = (
                    f"⚠️ {self._MAX_TOOL_ROUNDS}회 시도했지만 완료되지 않았습니다.\n"
                    f"원인: {last_errors}\n"
                )
                if action_summary:
                    msg += f"\n지금까지 실행된 작업:\n{action_summary}\n"
                msg += "\n계속 시도하시겠습니까?"
                self.conversation.append({"role": "assistant", "content": msg})
                return msg

            # 같은 도구+같은 파라미터 연속 호출 감지
            # (같은 도구라도 파라미터가 다르면 정상 — 예: L자 브래킷의 스케치 2회)
            current_keys = [
                f"{tc.name}:{json.dumps(tc.input, sort_keys=True, ensure_ascii=False)}"
                for tc in response.tool_calls
            ]
            if (current_keys
                    and all(k == current_keys[0] for k in current_keys)
                    and recent_tool_keys
                    and recent_tool_keys[-1] == current_keys[0]):
                recent_tool_keys.append(current_keys[0])
            elif current_keys and all(k == current_keys[0] for k in current_keys):
                recent_tool_keys = [current_keys[0]]
            else:
                recent_tool_keys.clear()

            if len(recent_tool_keys) >= self._MAX_SAME_TOOL_REPEAT:
                repeated = response.tool_calls[0].name
                logger.warning("[conv_loop] 같은 도구+파라미터 %d회 연속: %s — plan_parser 폴백",
                               self._MAX_SAME_TOOL_REPEAT, repeated)
                # plan_parser 폴백 시도 (이미 성공한 단계는 내부에서 스킵)
                fallback = self._try_plan_parser_fallback(completed_actions)
                if fallback:
                    return fallback
                # 폴백 실패 시 경고 반환
                summary = (
                    f"⚠️ 작업이 완료되지 않았습니다.\n"
                    f"같은 도구({repeated})가 동일 파라미터로 {self._MAX_SAME_TOOL_REPEAT}회 반복 호출되어 중단했습니다.\n"
                )
                if completed_actions:
                    summary += self._summarize_completed_actions(completed_actions) + "\n"
                summary += "\n요청을 더 구체적으로 다시 시도해주세요."
                self.conversation.append({"role": "assistant", "content": summary})
                return summary

            # 도구 실행
            assistant_msg = self._build_assistant_message(response)
            tool_results: list[ToolResultMessage] = []
            script_failed = False

            for tc in response.tool_calls:
                logger.info("Tool call: %s(%s)",
                            tc.name, json.dumps(tc.input, ensure_ascii=False)[:200])

                # 중복 실행 방지: 같은 도구+같은 파라미터로 이미 성공 → 실행 건너뛰고 캐시 결과 반환
                dedup_key = f"{tc.name}:{json.dumps(tc.input, sort_keys=True, ensure_ascii=False)}"
                logger.info("[conv_loop] dedup_key: %s (cache size: %d, hit: %s)",
                            dedup_key[:120], len(success_cache), dedup_key in success_cache)
                if dedup_key in success_cache:
                    logger.info("[conv_loop] 중복 호출 감지 — 실행 건너뛰고 캐시 반환: %s", tc.name)
                    cached = json.loads(success_cache[dedup_key])
                    result_content = json.dumps({
                        **cached,
                        "_dedup_notice": (
                            f"이 작업({tc.name})은 이미 성공적으로 완료되었습니다. "
                            "다시 실행하지 않았습니다. 다음 단계를 진행하세요."
                        ),
                    }, ensure_ascii=False)
                    tool_results.append(ToolResultMessage(
                        tool_call_id=tc.id,
                        content=result_content,
                        tool_name=tc.name,
                    ))
                    # completed_actions에는 추가하지 않음 (이미 기록됨)
                    continue

                result = self._route_tool_call(tc.name, tc.input)

                # 도구 실패 시 에러를 결과에 포함하여 LLM이 재시도/대안을 판단하도록 유도
                status = result.get("status", "unknown")
                if status == "failure" and tc.name.endswith("__execute_script"):
                    error_msg = result.get("error", "Unknown error")
                    stdout_msg = result.get("result", {}).get("stdout", "")
                    logger.warning(
                        "[conv_loop] execute_script 실패:\n  error: %s\n  stdout: %s",
                        error_msg[:500], stdout_msg[:200],
                    )
                    fix_hints = self._extract_fix_hints(error_msg)
                    hint_text = "\n".join(f"- {h}" for h in fix_hints) if fix_hints else ""

                    result_content = json.dumps({
                        **result,
                        "_retry_hint": (
                            f"스크립트 실행 실패.\n"
                            f"에러: {error_msg}\n"
                            + (f"stdout: {stdout_msg}\n" if stdout_msg else "")
                            + (f"\n구체적 수정 사항:\n{hint_text}\n" if hint_text else "")
                            + "\n에러를 수정한 코드로 fusion360__execute_script를 다시 호출하세요. "
                            "반드시 print(json.dumps({...}))로 결과를 출력하세요."
                        ),
                    }, ensure_ascii=False)
                    script_failed = True
                elif status == "failure":
                    error_msg = result.get("error", "Unknown error")
                    logger.warning("[conv_loop] 도구 실패: %s — %s",
                                   tc.name, error_msg[:300])
                    result_content = json.dumps({
                        **result,
                        "_retry_hint": (
                            f"도구 실행 실패: {tc.name}\n"
                            f"에러: {error_msg}\n"
                            "\n이 에러를 분석하고 필요한 선행 작업을 먼저 수행하세요. "
                            "예: 바디가 없으면 먼저 스케치+돌출로 바디를 생성한 뒤 재시도하세요."
                        ),
                    }, ensure_ascii=False)
                else:
                    result_content = json.dumps(result, ensure_ascii=False)

                tool_results.append(ToolResultMessage(
                    tool_call_id=tc.id,
                    content=result_content,
                    tool_name=tc.name,
                ))

                # 성공한 호출을 캐시에 저장 (execute_script 제외 — 매번 다를 수 있음)
                if status == "success" and not tc.name.endswith("__execute_script"):
                    success_cache[dedup_key] = result_content
                    logger.info("[conv_loop] 캐시 저장: %s", dedup_key[:120])

                completed_actions.append({
                    "tool": tc.name,
                    "status": status,
                    "input": tc.input,
                    "result": result.get("result", {}),
                })

                # 연속 같은 에러 추적
                if status == "failure":
                    err_key = result.get("error", "")[:100]
                    if recent_errors and recent_errors[-1] == err_key:
                        recent_errors.append(err_key)
                    else:
                        recent_errors = [err_key]
                else:
                    recent_errors.clear()

            # 같은 에러 3회 연속 → plan_parser 폴백 후 중단
            if len(recent_errors) >= self._MAX_SAME_TOOL_REPEAT:
                logger.warning("[conv_loop] 같은 에러 %d회 연속 — plan_parser 폴백: %s",
                               len(recent_errors), recent_errors[-1][:100])
                fallback = self._try_plan_parser_fallback(completed_actions)
                if fallback:
                    return fallback
                summary = (
                    f"⚠️ 같은 에러가 {len(recent_errors)}회 반복되어 중단합니다.\n"
                    f"에러: {recent_errors[-1]}\n"
                )
                if completed_actions:
                    summary += self._summarize_completed_actions(completed_actions) + "\n"
                summary += "\n요청을 수정하여 다시 시도해주세요."
                self.conversation.append({"role": "assistant", "content": summary})
                return summary

            new_messages = self.llm.format_tool_results(assistant_msg, tool_results)
            self.conversation.extend(new_messages)

            # execute_script 성공 시 즉시 종료 (재호출 방지)
            for idx, tc in enumerate(response.tool_calls):
                if not tc.name.endswith("__execute_script"):
                    continue
                tc_result = json.loads(tool_results[idx].content)
                if tc_result.get("status") == "success":
                    logger.info("[conv_loop] execute_script 성공 — 즉시 종료")
                    summary = self._summarize_completed_actions(completed_actions)
                    self.conversation.append({"role": "assistant", "content": summary})
                    return summary

            # 단일 터미널 도구 성공 시 즉시 종료
            # — Gemini가 후속으로 엉뚱한 도구를 호출하는 것 방지
            # (예: set_material 성공 후 불필요한 스케치+돌출 호출)
            if (len(response.tool_calls) == 1
                    and not script_failed
                    and completed_actions
                    and completed_actions[-1].get("status") == "success"):
                last_tool = completed_actions[-1]["tool"]
                if any(last_tool.endswith(t) for t in self._TERMINAL_TOOLS):
                    logger.info("[conv_loop] 터미널 도구 성공 — 즉시 종료: %s", last_tool)
                    summary = self._summarize_completed_actions(completed_actions)
                    self.conversation.append({"role": "assistant", "content": summary})
                    return summary


    @staticmethod
    def _extract_fix_hints(error_msg: str) -> list[str]:
        """에러 메시지에서 구체적인 수정 힌트를 추출한다."""
        import re
        hints: list[str] = []

        # "Did you mean: XXX?" 패턴
        m = re.search(r"Did you mean:\s*['\"]?(\w+)['\"]?", error_msg)
        if m:
            suggestion = m.group(1)
            # 원래 잘못 사용한 이름 추출
            attr_m = re.search(r"has no attribute ['\"](\w+)['\"]", error_msg)
            if attr_m:
                wrong = attr_m.group(1)
                hints.append(f"{wrong} → {suggestion} 으로 변경하세요")

        # "takes N positional arguments but M were given"
        m = re.search(r"(\w+)\(\) takes (?:from )?(\d+)(?: to (\d+))? positional arguments? but (\d+) (?:was|were) given", error_msg)
        if m:
            func = m.group(1)
            expected = m.group(3) or m.group(2)
            given = m.group(4)
            hints.append(f"{func}()에 인자 {given}개를 넣었지만 최대 {expected}개만 허용됩니다. 초과 인자를 제거하세요")

        # "has no attribute 'XXX'" (일반)
        if not hints:
            m = re.search(r"has no attribute ['\"](\w+)['\"]", error_msg)
            if m:
                hints.append(f"'{m.group(1)}' 속성이 존재하지 않습니다. API 문서를 확인하세요")

        # "NameError: name 'XXX' is not defined"
        m = re.search(r"NameError: name ['\"](\w+)['\"] is not defined", error_msg)
        if m:
            hints.append(f"'{m.group(1)}'가 정의되지 않았습니다. import 또는 변수 선언을 확인하세요")

        # SyntaxError
        m = re.search(r"SyntaxError: (.+?)(?:\n|$)", error_msg)
        if m:
            hints.append(f"문법 오류: {m.group(1)}")

        # Fusion API 관련 일반 힌트
        if "for " in error_msg and "is not iterable" in error_msg:
            hints.append("Fusion 컬렉션은 for-in 순회 불가. for i in range(collection.count): item = collection.item(i) 사용")

        if "createInput" in error_msg and "arguments" in error_msg:
            hints.append("createInput 인자 수를 확인하세요. shellFeatures.createInput(faces, isInside)은 2인자만 받습니다")

        return hints

    @staticmethod
    def _format_action_line(action: dict) -> str:
        """단일 도구 실행 결과를 한 줄 요약으로 포맷한다."""
        tool = action.get("tool", "")
        status = action.get("status", "")
        inp = action.get("input", {})
        res = action.get("result", {})
        short = tool.split("__")[-1] if "__" in tool else tool

        if status != "success":
            err = action.get("result", {}).get("error", status)
            return f"❌ {short}: {err}"

        # 도구별 상세 포맷
        if "rectangle_sketch" in short or "create_sketch" in short:
            w = inp.get("width_mm", res.get("width_mm", ""))
            h = inp.get("height_mm", res.get("height_mm", ""))
            name = res.get("sketch_name", "")
            if w and h:
                return f"✅ {w}×{h}mm 스케치 생성 완료" + (f" ({name})" if name else "")
            return f"✅ 스케치 생성 완료" + (f" ({name})" if name else "")

        if "circle_sketch" in short:
            r = inp.get("radius_mm", res.get("radius_mm", ""))
            name = res.get("sketch_name", "")
            if r:
                return f"✅ 반지름 {r}mm 원형 스케치 생성 완료" + (f" ({name})" if name else "")
            return f"✅ 원형 스케치 생성 완료"

        if short == "extrude":
            dist = inp.get("distance_mm", "")
            vol = res.get("volume_mm3", "")
            line = f"✅ {dist}mm 돌출 완료" if dist else "✅ 돌출 완료"
            if vol:
                line += f", 부피: {vol}mm³"
            return line

        if short == "set_material":
            mat = res.get("material_name", inp.get("material_name", ""))
            body = res.get("body_name", "")
            if mat:
                return f"✅ 재질 적용: {mat}" + (f" ({body})" if body else "")
            return "✅ 재질 적용 완료"

        if short == "fillet":
            r = inp.get("radius_mm", "")
            cnt = res.get("edge_count", "")
            line = f"✅ 필렛 {r}mm" if r else "✅ 필렛 적용"
            if cnt:
                line += f" ({cnt}개 엣지)"
            return line

        if short == "chamfer":
            d = inp.get("distance_mm", "")
            cnt = res.get("edge_count", "")
            line = f"✅ 챔퍼 {d}mm" if d else "✅ 챔퍼 적용"
            if cnt:
                line += f" ({cnt}개 엣지)"
            return line

        if "export_step" in short or "export_stl" in short:
            path = res.get("file_path", res.get("path", ""))
            fmt = "STEP" if "step" in short else "STL"
            return f"✅ {fmt} 내보내기 완료" + (f": {path}" if path else "")

        if "execute_script" in short:
            desc = (res.get("description") or res.get("message")
                    or res.get("body_name") or "스크립트 실행 완료")
            line = f"✅ {desc}"
            vol = res.get("volume_mm3")
            if vol:
                line += f" (부피: {vol}mm³)"
            return line

        if "create_hole" in short:
            d = inp.get("diameter_mm", "")
            depth = inp.get("depth_mm", "")
            return f"✅ 홀 생성 (직경 {d}mm, 깊이 {depth}mm)" if d else "✅ 홀 생성 완료"

        if short == "create_component":
            name = res.get("component_name", "")
            return f"✅ 컴포넌트 생성: {name}" if name else "✅ 컴포넌트 생성 완료"

        # 기본 폴백
        desc = (res.get("description") or res.get("message")
                or res.get("body_name") or res.get("material_name")
                or res.get("feature_name") or short)
        return f"✅ {desc}"

    @staticmethod
    def _collect_recent_errors(actions: list[dict]) -> str:
        """최근 실패한 도구 호출에서 에러 원인을 요약한다."""
        failed = [a for a in actions if a.get("status") == "failure"]
        if not failed:
            return "반복적인 도구 호출이 성공하지 못했습니다."
        last = failed[-1]
        error = last.get("result", {}).get("error", "")
        tool = last.get("tool", "unknown")
        if error:
            return f"{tool} 실패 — {error[:200]}"
        return f"{tool}이(가) 반복 실패했습니다."

    def _summarize_completed_actions(self, actions: list[dict]) -> str:
        """실행된 도구 목록을 상세 요약한다."""
        if not actions:
            return ""
        lines = [self._format_action_line(a) for a in actions]
        return "\n".join(lines)

    def _build_assistant_message(self, response) -> dict[str, Any]:
        """LLM 응답에서 assistant 메시지를 구성한다."""
        if response.raw is not None:
            if hasattr(response.raw, "content") and hasattr(response.raw, "stop_reason"):
                return {"role": "assistant", "content": response.raw.content}
            if hasattr(response.raw, "candidates") and response.raw.candidates:
                candidate = response.raw.candidates[0]
                parts: list[dict[str, Any]] = []
                for part in candidate.content.parts:
                    if part.text:
                        parts.append({"type": "text", "text": part.text})
                    elif part.function_call:
                        parts.append({
                            "function_call": {
                                "name": part.function_call.name,
                                "args": dict(part.function_call.args) if part.function_call.args else {},
                            },
                        })
                return {"role": "assistant", "parts": parts}

        return {"role": "assistant", "content": response.text}

    def add_user_response(self, message: str) -> str:
        """사용자 후속 응답을 추가하고 대화를 계속한다."""
        return self.run(message)

    def health_check_all(self) -> dict[str, bool]:
        """모든 어댑터의 health check를 실행한다."""
        results = {}
        for name, adapter in self.adapters.items():
            try:
                results[name] = adapter.health_check()
            except Exception as e:
                logger.error("Health check failed for %s: %s", name, e)
                results[name] = False
        return results

    def get_state(self) -> dict:
        """현재 파이프라인 상태를 반환한다."""
        return self.state.to_dict()

    def reset(self) -> None:
        """대화 및 상태를 초기화한다."""
        self.conversation.clear()
        self.state = PipelineState()


def _run_web_server(host: str = "127.0.0.1", port: int = 18081) -> None:
    """오케스트레이터를 HTTP 서버로 실행한다 (FusionBridge 중계용).

    POST /api/chat  {"message": "..."} → {"reply": "...", "status": "success|error"}
    GET  /api/health → {"status": "ok", "adapters": {...}}
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler

    logger.info("오케스트레이터 초기화 중...")
    orch = Orchestrator()
    logger.info("초기화 완료: adapters=%s, tools=%d, llm=%s",
                list(orch.adapters.keys()), len(orch.tools), type(orch.llm).__name__)

    class OrchestratorHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            path = self.path.rstrip("/")
            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

            if path == "/api/chat":
                self._handle_chat(raw)
            else:
                self._send_json(404, {"error": f"Not found: {path}"})

        def do_GET(self):
            path = self.path.rstrip("/")
            if path == "/api/health":
                health = orch.health_check_all()
                self._send_json(200, {"status": "ok", "adapters": health})
            else:
                self._send_json(404, {"error": f"Not found: {path}"})

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors_headers()
            self.end_headers()

        def _handle_chat(self, raw: bytes):
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json(400, {"reply": "Invalid JSON", "status": "error"})
                return

            message = body.get("message", "").strip()
            if not message:
                self._send_json(400, {"reply": "Empty message", "status": "error"})
                return

            logger.info("=== /api/chat 수신: %s", message[:200])
            logger.info("현재 대화 이력: %d턴", len(orch.conversation))

            try:
                reply = orch.run(message)
                logger.info("응답 길이: %d, 앞 200자: %s", len(reply or ""), (reply or "")[:200])
                self._send_json(200, {"reply": reply or "(빈 응답)", "status": "success"})
            except Exception as e:
                logger.exception("Chat error")
                self._send_json(200, {"reply": f"오류: {e}", "status": "error"})

        def _cors_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _send_json(self, code: int, data: dict):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self._cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            logger.debug("HTTP %s", format % args)

    server = HTTPServer((host, port), OrchestratorHandler)
    print(f"=== 오케스트레이터 웹서버 시작 ===")
    print(f"http://{host}:{port}")
    print("POST /api/chat  - chat")
    print("GET  /api/health - status")
    print()

    health = orch.health_check_all()
    if health:
        for name, ok in health.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    else:
        print("  등록된 어댑터가 없습니다.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
        server.shutdown()


def _run_terminal() -> None:
    """터미널 대화 모드."""
    orch = Orchestrator()

    print("=== AI CAD/EDA 통합 설계 시스템 ===")
    print("어댑터 상태 확인 중...")
    health = orch.health_check_all()
    if health:
        for name, ok in health.items():
            status = "OK" if ok else "FAIL"
            print(f"  [{status}] {name}")
    else:
        print("  등록된 어댑터가 없습니다.")
    print()
    print('"exit" 또는 "quit"을 입력하면 종료합니다.')
    print()

    while True:
        try:
            user_input = input("사용자> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("종료합니다.")
            break

        try:
            reply = orch.run(user_input)
            print(f"\nAI> {reply}\n")
        except Exception as e:
            logger.exception("오류 발생")
            print(f"\n[오류] {e}\n")


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if "--web" in sys.argv:
        _run_web_server()
    else:
        _run_terminal()
