"""conversation loop 재시도/복구 흐름 테스트."""

import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from orchestrator.llm_client import LLMResponse, ToolCall, ToolResultMessage


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


class TestMaxToolRoundsMessage:
    """20회 반복 도달 시 특수 메시지를 반환하는지 테스트."""

    def test_returns_retry_prompt_on_max_rounds(self, orch):
        """_MAX_TOOL_ROUNDS 초과 시 '완료되지 않았습니다' + '계속 시도' 포함 메시지."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id=f"c{call_count}", name="dummy_tool",
                                     input={"v": call_count})],
                stop_reason="tool_use",
            )

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={"status": "success"})

        # 대화 시작
        orch.conversation = [{"role": "user", "content": "복잡한 요청"}]
        result = orch._conversation_loop()

        assert "완료되지 않았습니다" in result
        assert "계속 시도" in result

    def test_retry_prompt_includes_error_cause(self, orch):
        """같은 에러 3회 연속 시 에러 원인이 메시지에 포함된다."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id=f"c{call_count}", name="execute_script",
                                     input={"code": f"v{call_count}"})],
                stop_reason="tool_use",
            )

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={
            "status": "failure",
            "error": "RuntimeError: sketch profile is empty",
            "result": {},
        })

        orch.conversation = [{"role": "user", "content": "복잡한 요청"}]
        result = orch._conversation_loop()

        # 같은 에러 3회 반복으로 중단
        assert "반복" in result
        assert "중단" in result
        assert "sketch profile is empty" in result


class TestUserRetryChoices:
    """사용자의 '계속 시도', '건너뛰기', '중단' 메시지 처리 테스트."""

    def test_continue_resets_loop(self, orch):
        """'계속 시도해줘' 수신 시 conversation_loop를 다시 호출한다."""
        orch._conversation_loop = MagicMock(return_value="완료!")
        result = orch.run("계속 시도해줘")
        orch._conversation_loop.assert_called_once()
        assert result == "완료!"

    def test_skip_returns_skip_message(self, orch):
        """'건너뛰고 다음' 수신 시 건너뛰기 메시지 반환."""
        result = orch.run("이 작업은 건너뛰고 다음 진행해줘")
        assert "건너뛰었습니다" in result

    def test_stop_returns_stop_message(self, orch):
        """'작업 중단해줘' 수신 시 중단 메시지 반환."""
        result = orch.run("작업 중단해줘")
        assert "중단했습니다" in result


class TestCollectRecentErrors:
    """_collect_recent_errors 헬퍼 테스트."""

    def test_no_failures(self):
        from orchestrator.core import Orchestrator
        result = Orchestrator._collect_recent_errors([
            {"tool": "a", "status": "success", "result": {}},
        ])
        assert "성공하지 못했습니다" in result

    def test_with_failure(self):
        from orchestrator.core import Orchestrator
        result = Orchestrator._collect_recent_errors([
            {"tool": "a", "status": "success", "result": {}},
            {"tool": "fusion360__execute_script", "status": "failure",
             "result": {"error": "NameError: x is not defined"}},
        ])
        assert "NameError" in result
        assert "execute_script" in result


class TestFailureRecoveryFlow:
    """도구 실패 시 에러가 LLM에 전달되어 복구할 수 있는지 테스트."""

    def test_failure_continues_loop_and_sends_error_to_llm(self, orch):
        """일반 도구 failure → 에러를 LLM에 전달 → 루프 계속 → LLM이 복구 도구 호출.

        시나리오:
        round 1: fillet 호출 → failure "바디가 없습니다"
        round 2: LLM이 에러를 보고 텍스트 (all-failed이므로 재요청됨)
        round 3: 재요청 후 LLM이 sketch 호출 → 성공
        round 4: LLM이 완료 텍스트 반환
        """
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 첫 번째: fillet 호출 (실패할 것)
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(
                        id="call_fillet_1",
                        name="fusion360__fillet",
                        input={"radius_mm": 2, "all_edges": True},
                    )],
                    stop_reason="tool_use",
                )
            elif call_count == 2:
                # 두 번째: 에러를 보고 텍스트 (all-failed → 재요청됨)
                return LLMResponse(
                    text="바디가 없어서 먼저 박스를 만들겠습니다.",
                    stop_reason="end_turn",
                )
            elif call_count == 3:
                # 세 번째: 재요청 받고 extrude 도구 호출 (바디 생성)
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(
                        id="call_extrude_1",
                        name="fusion360__extrude",
                        input={"distance_mm": 25},
                    )],
                    stop_reason="tool_use",
                )
            else:
                # 네 번째: 완료 텍스트
                return LLMResponse(
                    text="모든 작업이 완료되었습니다. 바디를 생성했습니다.",
                    stop_reason="end_turn",
                )

        orch.llm.chat = fake_chat

        captured_tool_results = []
        call_idx = [0]

        def fake_format(assistant_msg, tool_results):
            captured_tool_results.extend(tool_results)
            return [
                assistant_msg,
                {"role": "user", "content": "tool result"},
            ]

        orch.llm.format_tool_results = fake_format

        def fake_route(name, inp):
            if "fillet" in name:
                return {
                    "status": "failure",
                    "error": "바디가 없습니다. 먼저 형상을 생성하세요.",
                    "result": {},
                }
            return {"status": "success", "result": {"description": "ok"}}

        orch._route_tool_call = fake_route

        orch.conversation = [{"role": "user", "content": "필렛 적용해줘"}]
        result = orch._conversation_loop()

        # fillet 실패 후 → 텍스트 → 재요청 → extrude 성공 → 완료 텍스트
        assert call_count == 4
        assert "완료" in result

        # 에러 결과가 LLM에 전달되었는지
        assert len(captured_tool_results) >= 1
        first_result = json.loads(captured_tool_results[0].content)
        assert first_result["status"] == "failure"
        assert "바디가 없습니다" in first_result["error"]
        assert "_retry_hint" in first_result

    def test_failure_retry_hint_includes_guidance(self, orch):
        """일반 도구 failure의 _retry_hint에 선행 작업 안내가 포함되는지."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(
                        id="call_chamfer_1",
                        name="fusion360__chamfer",
                        input={"distance_mm": 1, "all_edges": True},
                    )],
                    stop_reason="tool_use",
                )
            else:
                return LLMResponse(text="복구 완료", stop_reason="end_turn")

        orch.llm.chat = fake_chat

        captured_hints = []

        def fake_format(assistant_msg, tool_results):
            for tr in tool_results:
                content = json.loads(tr.content)
                if "_retry_hint" in content:
                    captured_hints.append(content["_retry_hint"])
            return [assistant_msg, {"role": "user", "content": "result"}]

        orch.llm.format_tool_results = fake_format
        orch._route_tool_call = MagicMock(return_value={
            "status": "failure",
            "error": "바디가 없습니다. 먼저 형상을 생성하세요.",
            "result": {},
        })

        orch.conversation = [{"role": "user", "content": "챔퍼 적용"}]
        orch._conversation_loop()

        assert len(captured_hints) == 1
        assert "선행 작업" in captured_hints[0]
        assert "바디를 생성" in captured_hints[0]

    def test_tool_result_includes_tool_name(self, orch):
        """ToolResultMessage에 tool_name이 올바르게 설정되는지."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(
                        id="call_fillet_1",
                        name="fusion360__fillet",
                        input={"radius_mm": 2, "all_edges": True},
                    )],
                    stop_reason="tool_use",
                )
            else:
                return LLMResponse(text="ok", stop_reason="end_turn")

        orch.llm.chat = fake_chat

        captured_results = []

        def fake_format(assistant_msg, tool_results):
            captured_results.extend(tool_results)
            return [assistant_msg, {"role": "user", "content": "result"}]

        orch.llm.format_tool_results = fake_format
        orch._route_tool_call = MagicMock(return_value={
            "status": "failure",
            "error": "바디가 없습니다.",
            "result": {},
        })

        orch.conversation = [{"role": "user", "content": "필렛"}]
        orch._conversation_loop()

        assert len(captured_results) == 1
        assert captured_results[0].tool_name == "fusion360__fillet"


class TestContinuousExecution:
    """승인 후 모든 단계를 연속 실행하는지 테스트."""

    def test_intermediate_text_triggers_continuation(self, orch):
        """중간 텍스트(완료 아닌)는 나머지 단계 실행을 재요청한다.

        시나리오:
        round 1: sketch 호출 → 성공
        round 2: "스케치를 만들었습니다" 텍스트 (완료가 아님) → 재요청
        round 3: extrude 호출 → 성공
        round 4: "모든 작업이 완료되었습니다" 텍스트 → 종료
        """
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="fusion360__create_rectangle_sketch",
                                         input={"width_mm": 60, "height_mm": 40})],
                    stop_reason="tool_use",
                )
            elif call_count == 2:
                # 중간 텍스트 (완료 키워드 없음)
                return LLMResponse(
                    text="스케치를 만들었습니다. 다음으로 돌출합니다.",
                    stop_reason="end_turn",
                )
            elif call_count == 3:
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c2", name="fusion360__extrude",
                                         input={"distance_mm": 25})],
                    stop_reason="tool_use",
                )
            else:
                return LLMResponse(
                    text="모든 작업이 완료되었습니다. 60x40x25mm 박스가 생성되었습니다.",
                    stop_reason="end_turn",
                )

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={"status": "success", "result": {}})

        orch.conversation = [{"role": "user", "content": "박스 만들어줘"}]
        result = orch._conversation_loop()

        # 중간 텍스트에서 끝나지 않고 4번째까지 진행
        assert call_count == 4
        assert "완료" in result

    def test_completion_text_ends_loop(self, orch):
        """터미널 도구(fillet) 성공 시 즉시 loop을 종료한다."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="fusion360__fillet",
                                         input={"radius_mm": 2, "all_edges": True})],
                    stop_reason="tool_use",
                )
            else:
                return LLMResponse(
                    text="필렛이 성공적으로 적용되었습니다.",
                    stop_reason="end_turn",
                )

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={"status": "success", "result": {}})

        orch.conversation = [{"role": "user", "content": "필렛 적용"}]
        result = orch._conversation_loop()

        # fillet은 터미널 도구 → 성공 즉시 종료 (LLM 추가 호출 없음)
        assert call_count == 1
        assert "✅" in result

    def test_three_step_continuous_execution(self, orch):
        """스케치→돌출→필렛 3단계에서 매 단계 후 중간 텍스트가 와도 끝까지 진행.

        시나리오 (실제 Gemini 동작 시뮬레이션):
        call 1: sketch 호출 → 성공
        call 2: "스케치 생성됨" 텍스트 → nudge
        call 3: extrude 호출 → 성공
        call 4: "돌출 생성됨" 텍스트 → nudge
        call 5: fillet 호출 → 성공
        call 6: "모든 작업이 완료되었습니다" 텍스트 → 종료
        """
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c1", name="fusion360__create_rectangle_sketch",
                                         input={"width_mm": 60, "height_mm": 40})],
                )
            elif call_count == 2:
                return LLMResponse(text="스케치가 생성되었습니다.", stop_reason="end_turn")
            elif call_count == 3:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c2", name="fusion360__extrude",
                                         input={"distance_mm": 25})],
                )
            elif call_count == 4:
                return LLMResponse(text="25mm 돌출하여 박스를 만들었습니다.", stop_reason="end_turn")
            elif call_count == 5:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c3", name="fusion360__fillet",
                                         input={"radius_mm": 2, "all_edges": True})],
                )
            else:
                return LLMResponse(
                    text="모든 작업이 완료되었습니다. 60x40x25mm 박스에 2mm 필렛 적용.",
                    stop_reason="end_turn",
                )

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={"status": "success", "result": {}})

        orch.conversation = [{"role": "user", "content": "박스 만들고 필렛"}]
        result = orch._conversation_loop()

        # fillet은 터미널 도구 → 성공 즉시 종료 (call 6 불필요)
        assert call_count == 5
        assert "✅" in result
        # 도구가 3번 호출됨
        assert orch._route_tool_call.call_count == 3

    def test_nudge_counter_resets_on_tool_call(self, orch):
        """도구 호출 성공 후 continuation_nudges 카운터가 리셋되는지 확인.

        nudge 후 도구 호출 → nudge 카운터 리셋 → 다시 nudge 가능
        이렇게 해야 3단계 이상도 동작함.
        """
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            # 패턴: tool → text → tool → text → tool → text → ... → "완료" text
            # 6단계까지 (tool 3번 + 중간 text 3번)
            if call_count in (1, 3, 5):
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id=f"c{call_count}", name=f"tool_{call_count}",
                                         input={"step": call_count})],
                )
            elif call_count == 6:
                return LLMResponse(text="모든 작업 완료.", stop_reason="end_turn")
            else:
                return LLMResponse(text="진행 중입니다.", stop_reason="end_turn")

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={"status": "success", "result": {}})

        orch.conversation = [{"role": "user", "content": "테스트"}]
        result = orch._conversation_loop()

        assert call_count == 6
        assert "완료" in result

    def test_no_actions_text_ends_loop(self, orch):
        """도구 실행 이력 없이 텍스트가 오면 (계획 제시 등) 바로 반환."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            return LLMResponse(
                text="60x40x25mm 박스를 만들겠습니다. 진행할까요?",
                stop_reason="end_turn",
            )

        orch.llm.chat = fake_chat
        orch.conversation = [{"role": "user", "content": "박스 만들어줘"}]
        result = orch._conversation_loop()

        assert call_count == 1
        assert "진행할까요" in result


class TestDedupContinuation:
    """중복 호출 감지 시 loop 종료 대신 계속 진행하는지 테스트."""

    def test_dedup_returns_cached_result_and_continues(self, orch):
        """sketch 중복 호출 시 실행하지 않고 캐시 결과를 반환, extrude로 진행.

        시나리오:
        round 1: sketch 호출 → 성공 (캐시에 저장)
        round 2: sketch 같은 파라미터 재호출 → 캐시 히트 → "이미 완료" 반환
        round 3: Gemini가 extrude 호출 → 성공
        round 4: "완료" 텍스트
        """
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c1", name="fusion360__create_rectangle_sketch",
                                         input={"width_mm": 60, "height_mm": 40})],
                )
            elif call_count == 2:
                # 같은 스케치를 다시 호출 (Gemini 실수)
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c2", name="fusion360__create_rectangle_sketch",
                                         input={"width_mm": 60, "height_mm": 40})],
                )
            elif call_count == 3:
                # "이미 완료" 메시지를 보고 extrude로 진행
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c3", name="fusion360__extrude",
                                         input={"distance_mm": 25})],
                )
            else:
                return LLMResponse(
                    text="모든 작업이 완료되었습니다.",
                    stop_reason="end_turn",
                )

        orch.llm.chat = fake_chat

        actual_calls = []

        def fake_format(assistant_msg, tool_results):
            for tr in tool_results:
                content = json.loads(tr.content)
                actual_calls.append({
                    "name": tr.tool_name,
                    "dedup": "_dedup_notice" in content,
                })
            return [assistant_msg, {"role": "user", "content": "result"}]

        orch.llm.format_tool_results = fake_format
        orch._route_tool_call = MagicMock(return_value={
            "status": "success",
            "result": {"sketch_name": "Sketch1"},
        })

        orch.conversation = [{"role": "user", "content": "박스 만들어줘"}]
        result = orch._conversation_loop()

        # loop이 종료되지 않고 4번째까지 진행
        assert call_count == 4
        assert "완료" in result

        # 실제 도구 실행은 2번 (sketch 1번 + extrude 1번), 중복 sketch는 실행 안 함
        assert orch._route_tool_call.call_count == 2

        # format_tool_results에 중복 결과가 전달됨
        assert actual_calls[0] == {"name": "fusion360__create_rectangle_sketch", "dedup": False}
        assert actual_calls[1] == {"name": "fusion360__create_rectangle_sketch", "dedup": True}
        assert actual_calls[2] == {"name": "fusion360__extrude", "dedup": False}


class TestPlanParserFallback:
    """Gemini 빈 응답 시 plan_parser 폴백 테스트."""

    def test_empty_response_triggers_plan_parser(self, orch):
        """빈 응답 반복 후 plan_parser가 계획을 실행한다.

        시나리오: 사용자가 "60x40x25mm 박스"를 요청하고 승인함.
        Gemini가 빈 응답을 반복 → plan_parser가 대화에서 치수를 추출하여 실행.
        """
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            # 계속 빈 응답 (Gemini 실패 시뮬레이션)
            return LLMResponse(text="", stop_reason="end_turn")

        orch.llm.chat = fake_chat
        orch._route_tool_call = MagicMock(return_value={
            "status": "success",
            "result": {"sketch_name": "Sketch1", "body_name": "Body1"},
        })

        # 대화 이력: 계획 + 승인
        orch.conversation = [
            {"role": "user", "content": "60x40x25mm 박스 만들어줘"},
            {"role": "assistant", "content":
                "이렇게 만들겠습니다:\n1. 60x40mm 사각 스케치 생성\n2. 25mm 돌출\n진행할까요?"},
            {"role": "user", "content": "승인"},
        ]
        result = orch._conversation_loop()

        # plan_parser가 실행됨 (빈 응답 → 재요청 → 빈 응답 → 폴백)
        assert orch._route_tool_call.call_count >= 1
        assert "✅" in result or "스케치" in result.lower() or "돌출" in result.lower()

    def test_plan_parser_executes_remaining_steps(self, orch):
        """Gemini가 1단계만 실행하고 멈추면 plan_parser가 나머지를 실행."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 첫 번째: 스케치만 호출
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c1", name="fusion360__create_rectangle_sketch",
                                         input={"width_mm": 60, "height_mm": 40})],
                )
            else:
                # 이후: 빈 응답 (체이닝 실패)
                return LLMResponse(text="", stop_reason="end_turn")

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={
            "status": "success",
            "result": {"sketch_name": "Sketch1"},
        })

        orch.conversation = [
            {"role": "user", "content": "60x40x25mm 박스 만들어줘"},
            {"role": "assistant", "content":
                "이렇게 만들겠습니다:\n1. 60x40mm 사각 스케치\n2. 25mm 돌출\n진행할까요?"},
            {"role": "user", "content": "그래"},
        ]
        result = orch._conversation_loop()

        # sketch (Gemini) + plan_parser 폴백으로 추가 실행
        assert orch._route_tool_call.call_count >= 2

    def test_execute_parsed_steps_runs_all(self, orch):
        """_execute_parsed_steps가 모든 단계를 순차 실행."""
        from orchestrator.plan_parser import ParsedStep

        orch._route_tool_call = MagicMock(return_value={
            "status": "success",
            "result": {"body_name": "Body1"},
        })

        steps = [
            ParsedStep(order=1, tool_name="fusion360__create_rectangle_sketch",
                       action="create_rectangle_sketch", adapter="fusion360",
                       parameters={"width_mm": 60, "height_mm": 40},
                       description="60x40mm 스케치"),
            ParsedStep(order=2, tool_name="fusion360__extrude",
                       action="extrude", adapter="fusion360",
                       parameters={"distance_mm": 25},
                       description="25mm 돌출"),
        ]

        result = orch._execute_parsed_steps(steps)

        assert orch._route_tool_call.call_count == 2
        assert "✅" in result
        assert "스케치" in result
        assert "돌출" in result

    def test_same_tool_repeat_triggers_plan_fallback(self, orch):
        """같은 도구 3회 반복 시 plan_parser 폴백으로 나머지 단계를 실행.

        시나리오: Gemini가 sketch를 3번 반복 호출 → 감지 → plan_parser 폴백
        → 대화에서 "60x40mm 스케치 + 25mm 돌출 + 2mm 필렛" 파싱
        → 이미 성공한 sketch 건너뛰고 extrude + fillet 실행
        """
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            # 매번 같은 sketch 호출 (Gemini 체이닝 실패)
            return LLMResponse(
                text="", stop_reason="tool_use",
                tool_calls=[ToolCall(id=f"c{call_count}",
                                     name="fusion360__create_rectangle_sketch",
                                     input={"width_mm": 60, "height_mm": 40})],
            )

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "tool call"},
            {"role": "user", "content": "tool result data"},
        ])
        orch._route_tool_call = MagicMock(return_value={
            "status": "success",
            "result": {"sketch_name": "Sketch1"},
        })

        orch.conversation = [
            {"role": "user", "content": "60x40x25mm 박스 만들고 모서리 전부 2mm 필렛 적용해줘"},
            {"role": "assistant", "content":
                "이렇게 만들겠습니다:\n"
                "1. 60x40mm 사각 스케치 생성\n"
                "2. 25mm 돌출\n"
                "3. 모든 모서리에 2mm 필렛\n"
                "진행할까요?"},
            {"role": "user", "content": "승인"},
        ]
        result = orch._conversation_loop()

        # plan_parser 폴백이 실행됨 (sketch는 이미 완료 → extrude + fillet 실행)
        assert "✅" in result
        # sketch 1회(Gemini) + plan_parser 폴백에서 추가 호출
        assert orch._route_tool_call.call_count >= 2


class TestTerminalToolEarlyExit:
    """터미널 도구(set_material, fillet 등) 성공 시 즉시 종료 테스트."""

    def test_set_material_exits_immediately(self, orch):
        """'알루미늄 재질 적용' → set_material 성공 → 추가 호출 없이 종료."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c1", name="fusion360__set_material",
                                         input={"material": "Aluminum"})],
                )
            else:
                # 이 호출은 도달하면 안 됨
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c2",
                                         name="fusion360__create_rectangle_sketch",
                                         input={"width_mm": 60, "height_mm": 40})],
                )

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={
            "status": "success",
            "result": {"material": "Aluminum"},
        })

        orch.conversation = [{"role": "user", "content": "알루미늄 재질 적용해줘"}]
        result = orch._conversation_loop()

        # set_material 성공 → 즉시 종료, 스케치 호출 없음
        assert call_count == 1
        assert "✅" in result
        assert orch._route_tool_call.call_count == 1

    def test_fillet_exits_immediately(self, orch):
        """'모든 모서리에 2mm 필렛' → fillet 성공 → 추가 호출 없이 종료."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c1", name="fusion360__fillet",
                                         input={"radius_mm": 2, "all_edges": True})],
                )
            else:
                return LLMResponse(text="필렛 완료", stop_reason="end_turn")

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={
            "status": "success", "result": {},
        })

        orch.conversation = [{"role": "user", "content": "모든 모서리에 2mm 필렛"}]
        result = orch._conversation_loop()

        assert call_count == 1
        assert "✅" in result

    def test_export_step_exits_immediately(self, orch):
        """'STEP 내보내줘' → export_step 성공 → 추가 호출 없이 종료."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c1", name="fusion360__export_step",
                                         input={"file_path": "output.step"})],
                )
            else:
                return LLMResponse(text="내보내기 완료", stop_reason="end_turn")

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={
            "status": "success",
            "result": {"file_path": "output.step"},
        })

        orch.conversation = [{"role": "user", "content": "STEP 내보내줘"}]
        result = orch._conversation_loop()

        assert call_count == 1
        assert "✅" in result

    def test_non_terminal_tool_continues_loop(self, orch):
        """스케치(비터미널 도구) 성공 시 loop이 계속 진행된다."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c1",
                                         name="fusion360__create_rectangle_sketch",
                                         input={"width_mm": 60, "height_mm": 40})],
                )
            elif call_count == 2:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c2", name="fusion360__extrude",
                                         input={"distance_mm": 25})],
                )
            else:
                return LLMResponse(
                    text="스케치와 돌출이 완료되었습니다.",
                    stop_reason="end_turn",
                )

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={
            "status": "success", "result": {},
        })

        orch.conversation = [{"role": "user", "content": "60x40x25mm 박스 만들어줘"}]
        result = orch._conversation_loop()

        # 스케치 → 돌출 → 완료 텍스트: 3번 호출
        assert call_count == 3
        assert orch._route_tool_call.call_count == 2

    def test_terminal_tool_failure_continues_loop(self, orch):
        """터미널 도구라도 실패하면 loop이 계속 진행된다 (즉시 종료 안 함)."""
        call_count = 0

        def fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="c1", name="fusion360__set_material",
                                         input={"material": "Aluminum"})],
                )
            else:
                return LLMResponse(text="재질 설정 실패 안내", stop_reason="end_turn")

        orch.llm.chat = fake_chat
        orch.llm.format_tool_results = MagicMock(return_value=[
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "result"},
        ])
        orch._route_tool_call = MagicMock(return_value={
            "status": "failure",
            "error": "바디가 없습니다.",
            "result": {},
        })

        orch.conversation = [{"role": "user", "content": "알루미늄 적용"}]
        result = orch._conversation_loop()

        # 실패 → 즉시 종료하지 않고 loop 계속 (call_count > 1)
        assert call_count > 1
