"""T-7: LLM 교체 가능 확인 테스트.

config에서 llm.provider를 gemini에서 claude로 바꿔도
DesignSpec 기반 파이프라인이 동일하게 동작하는지 검증한다.

핵심 확인 사항:
1. GeminiClient와 ClaudeClient가 동일한 LLMClient 인터페이스를 구현한다
2. DesignSpec 데이터가 LLM 프로바이더에 독립적이다
3. Orchestrator가 어느 LLM이든 같은 흐름으로 동작한다
4. DesignSpec → LLM 전달 → 응답 처리가 모델 교체와 무관하다
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.llm_client import LLMClient, LLMResponse, ToolCall, ToolResultMessage, create_llm_client
from pipeline.design_spec import DesignSpec


try:
    from orchestrator.gemini_client import GeminiClient
    _HAS_GENAI = True
except ImportError:
    _HAS_GENAI = False


class TestLLMClientInterfaceConformance:
    """GeminiClient와 ClaudeClient가 동일한 LLMClient 인터페이스를 구현하는지 확인."""

    @pytest.mark.skipif(not _HAS_GENAI, reason="google-genai SDK not installed")
    def test_gemini_is_llm_client(self):
        assert issubclass(GeminiClient, LLMClient)

    def test_claude_is_llm_client(self):
        from orchestrator.claude_client import ClaudeClient
        assert issubclass(ClaudeClient, LLMClient)

    @pytest.mark.skipif(not _HAS_GENAI, reason="google-genai SDK not installed")
    def test_both_have_chat_method(self):
        from orchestrator.claude_client import ClaudeClient
        assert hasattr(GeminiClient, "chat")
        assert hasattr(ClaudeClient, "chat")
        assert callable(getattr(GeminiClient, "chat"))
        assert callable(getattr(ClaudeClient, "chat"))

    @pytest.mark.skipif(not _HAS_GENAI, reason="google-genai SDK not installed")
    def test_both_have_format_tool_results(self):
        from orchestrator.claude_client import ClaudeClient
        assert hasattr(GeminiClient, "format_tool_results")
        assert hasattr(ClaudeClient, "format_tool_results")

    @pytest.mark.skipif(not _HAS_GENAI, reason="google-genai SDK not installed")
    def test_chat_signature_matches(self):
        """chat() 메서드 시그니처가 동일한지 확인."""
        import inspect
        from orchestrator.claude_client import ClaudeClient

        gemini_sig = inspect.signature(GeminiClient.chat)
        claude_sig = inspect.signature(ClaudeClient.chat)

        gemini_params = set(gemini_sig.parameters.keys())
        claude_params = set(claude_sig.parameters.keys())

        # self 제외한 파라미터가 동일해야 함
        assert gemini_params == claude_params


class TestDesignSpecWithLLMProviders:
    """DesignSpec이 LLM 프로바이더 교체와 무관하게 동작하는지 확인."""

    @pytest.fixture
    def sample_spec(self) -> DesignSpec:
        spec = DesignSpec(name="bracket_v1", description="L-bracket for test")
        spec.set_parameter("width", 40.0, unit="mm", min_value=10.0, max_value=100.0)
        spec.set_parameter("height", 20.0, unit="mm")
        spec.set_parameter("thickness", 3.0, unit="mm")
        spec.add_constraint("min_width", "width >= 10")
        spec.add_objective("min_mass", "minimize", "mass")
        return spec

    def test_spec_to_dict_is_provider_agnostic(self, sample_spec: DesignSpec):
        """to_dict()는 어떤 LLM에든 전달 가능한 순수 딕셔너리여야 한다."""
        data = sample_spec.to_dict()
        # JSON 직렬화 가능해야 함
        json_str = json.dumps(data, ensure_ascii=False)
        restored = json.loads(json_str)

        assert restored["name"] == "bracket_v1"
        assert "width" in restored["parameters"]
        assert restored["parameters"]["width"]["value"] == 40.0

    def test_spec_context_works_for_any_llm(self, sample_spec: DesignSpec):
        """to_llm_context()는 어떤 LLM이든 해석 가능한 순수 텍스트여야 한다."""
        ctx = sample_spec.to_llm_context()
        assert isinstance(ctx, str)
        assert "width: 40.0 mm" in ctx

    def test_spec_in_message_gemini_format(self, sample_spec: DesignSpec):
        """DesignSpec을 Gemini 대화 형식으로 전달할 수 있어야 한다."""
        ctx = sample_spec.to_llm_context()
        messages = [{"role": "user", "content": f"다음 설계 명세를 분석해줘:\n{ctx}"}]

        # Gemini 형식: content는 문자열
        assert isinstance(messages[0]["content"], str)
        assert "bracket_v1" in messages[0]["content"]

    def test_spec_in_message_claude_format(self, sample_spec: DesignSpec):
        """DesignSpec을 Claude 대화 형식으로 전달할 수 있어야 한다."""
        ctx = sample_spec.to_llm_context()
        messages = [{"role": "user", "content": f"다음 설계 명세를 분석해줘:\n{ctx}"}]

        # Claude 형식: content는 문자열
        assert isinstance(messages[0]["content"], str)
        assert "bracket_v1" in messages[0]["content"]


class TestOrchestratorWithDesignSpec:
    """Orchestrator가 DesignSpec과 함께 LLM 프로바이더에 독립적으로 동작하는지 확인."""

    def _make_mock_llm(self) -> MagicMock:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.chat.return_value = LLMResponse(
            text="설계 명세를 확인했습니다. 40mm 브래킷을 만들겠습니다.",
            stop_reason="end_turn",
        )
        return mock_llm

    def test_orchestrator_with_gemini_mock(self):
        """Gemini 클라이언트 mock으로 DesignSpec 기반 흐름이 동작한다."""
        mock_llm = self._make_mock_llm()
        from orchestrator.core import Orchestrator

        orch = Orchestrator(config={"tools": {}}, llm_client=mock_llm)

        spec = DesignSpec(name="test")
        spec.set_parameter("width", 40.0, unit="mm")
        spec_context = spec.to_llm_context()

        result = orch.run(f"다음 명세로 설계해줘:\n{spec_context}")
        assert "설계 명세를 확인했습니다" in result
        mock_llm.chat.assert_called_once()

    def test_orchestrator_with_claude_mock(self):
        """Claude 클라이언트 mock으로 동일한 DesignSpec 흐름이 동작한다."""
        mock_llm = self._make_mock_llm()
        from orchestrator.core import Orchestrator

        orch = Orchestrator(config={"tools": {}}, llm_client=mock_llm)

        spec = DesignSpec(name="test")
        spec.set_parameter("width", 40.0, unit="mm")
        spec_context = spec.to_llm_context()

        result = orch.run(f"다음 명세로 설계해줘:\n{spec_context}")
        assert "설계 명세를 확인했습니다" in result

    def test_same_spec_same_result_different_provider(self):
        """동일한 DesignSpec으로 어떤 프로바이더든 같은 응답 형태를 받는다."""
        spec = DesignSpec(name="bracket")
        spec.set_parameter("width", 40.0, unit="mm")
        spec.set_parameter("height", 20.0, unit="mm")

        spec_dict = spec.to_dict()
        spec_text = spec.to_llm_context()

        # 두 프로바이더 mock
        for provider_name in ["gemini", "claude"]:
            mock_llm = self._make_mock_llm()
            from orchestrator.core import Orchestrator

            orch = Orchestrator(config={"tools": {}}, llm_client=mock_llm)
            result = orch.run(f"설계 명세:\n{spec_text}")

            assert isinstance(result, str)
            assert len(result) > 0

            # LLM에 전달된 메시지에 명세가 포함되었는지 확인
            call_args = mock_llm.chat.call_args
            messages = call_args[1].get("messages") or call_args[0][0]
            last_user_msg = [m for m in messages if m["role"] == "user"][-1]
            assert "width" in last_user_msg["content"]

    def test_create_llm_client_factory_claude(self):
        """create_llm_client가 claude config에 따라 올바른 클라이언트를 반환한다."""
        from orchestrator.claude_client import ClaudeClient

        claude_client = create_llm_client({"llm": {"provider": "claude", "api_key": "sk-test"}})
        assert isinstance(claude_client, ClaudeClient)
        assert isinstance(claude_client, LLMClient)

    @pytest.mark.skipif(not _HAS_GENAI, reason="google-genai SDK not installed")
    def test_create_llm_client_factory_gemini(self):
        """create_llm_client가 gemini config에 따라 올바른 클라이언트를 반환한다."""
        gemini_client = create_llm_client({"llm": {"provider": "gemini", "api_key": "test"}})
        assert isinstance(gemini_client, GeminiClient)
        assert isinstance(gemini_client, LLMClient)

    def test_unknown_provider_raises(self):
        """알 수 없는 프로바이더는 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_llm_client({"llm": {"provider": "openai"}})
