"""LLM 클라이언트 추상화 테스트."""

from unittest.mock import MagicMock, patch

import pytest

from orchestrator.llm_client import (
    LLMClient,
    LLMResponse,
    ToolCall,
    ToolResultMessage,
    create_llm_client,
)


class TestLLMResponse:
    def test_has_tool_calls_true(self):
        resp = LLMResponse(
            text="",
            tool_calls=[ToolCall(id="1", name="test", input={})],
            stop_reason="tool_use",
        )
        assert resp.has_tool_calls is True

    def test_has_tool_calls_false(self):
        resp = LLMResponse(text="hello", stop_reason="end_turn")
        assert resp.has_tool_calls is False

    def test_default_values(self):
        resp = LLMResponse()
        assert resp.text == ""
        assert resp.tool_calls == []
        assert resp.stop_reason == ""
        assert resp.raw is None


class TestToolCall:
    def test_fields(self):
        tc = ToolCall(id="call_1", name="fusion360__create_sketch", input={"plane": "xy"})
        assert tc.id == "call_1"
        assert tc.name == "fusion360__create_sketch"
        assert tc.input == {"plane": "xy"}


class TestCreateLLMClient:
    def test_create_gemini_client(self):
        """gemini 프로바이더 설정 시 GeminiClient를 반환한다."""
        config = {"llm": {"provider": "gemini", "api_key": "test-key"}}
        client = create_llm_client(config)
        from orchestrator.gemini_client import GeminiClient
        assert isinstance(client, GeminiClient)

    def test_create_claude_client(self):
        """claude 프로바이더 설정 시 ClaudeClient를 반환한다."""
        config = {"llm": {"provider": "claude", "api_key": "sk-ant-test"}}
        client = create_llm_client(config)
        from orchestrator.claude_client import ClaudeClient
        assert isinstance(client, ClaudeClient)

    def test_default_provider_is_gemini(self):
        """llm 설정이 없으면 기본 gemini를 사용한다."""
        config = {}
        client = create_llm_client(config)
        from orchestrator.gemini_client import GeminiClient
        assert isinstance(client, GeminiClient)

    def test_unknown_provider_raises(self):
        config = {"llm": {"provider": "openai"}}
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_llm_client(config)


class TestGeminiClient:
    def test_inherits_llm_client(self):
        from orchestrator.gemini_client import GeminiClient
        assert issubclass(GeminiClient, LLMClient)

    def test_init_defaults(self):
        from orchestrator.gemini_client import GeminiClient
        client = GeminiClient({"api_key": "test"})
        assert client._model == "gemini-2.5-flash"

    def test_init_custom_model(self):
        from orchestrator.gemini_client import GeminiClient
        client = GeminiClient({"api_key": "test", "model": "gemini-2.5-pro"})
        assert client._model == "gemini-2.5-pro"

    def test_convert_tools(self):
        from orchestrator.gemini_client import GeminiClient
        client = GeminiClient({"api_key": "test"})
        tools = [
            {
                "name": "fusion360__create_sketch",
                "description": "Create a sketch",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "plane": {"type": "string", "enum": ["xy", "xz", "yz"]},
                    },
                    "required": ["plane"],
                },
            },
        ]
        gemini_tools = client._convert_tools(tools)
        assert len(gemini_tools) == 1
        assert len(gemini_tools[0].function_declarations) == 1
        assert gemini_tools[0].function_declarations[0].name == "fusion360__create_sketch"

    def test_convert_messages_simple(self):
        from orchestrator.gemini_client import GeminiClient
        client = GeminiClient({"api_key": "test"})
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        contents = client._convert_messages(messages)
        assert len(contents) == 2
        assert contents[0].role == "user"
        assert contents[1].role == "model"

    @patch("orchestrator.gemini_client.genai.Client")
    def test_chat_error_handling(self, mock_client_cls):
        from orchestrator.gemini_client import GeminiClient
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance
        mock_instance.models.generate_content.side_effect = Exception("API error")

        client = GeminiClient({"api_key": "test"})
        client._client = mock_instance
        resp = client.chat([{"role": "user", "content": "test"}])
        assert resp.stop_reason == "error"
        assert "API error" in resp.text

    def test_format_tool_results(self):
        from orchestrator.gemini_client import GeminiClient
        client = GeminiClient({"api_key": "test"})
        assistant_msg = {"role": "assistant", "parts": [{"function_call": {"name": "test", "args": {}}}]}
        results = [ToolResultMessage(tool_call_id="test", content='{"status": "success"}')]
        messages = client.format_tool_results(assistant_msg, results)
        assert len(messages) == 2
        assert messages[0] == assistant_msg
        assert messages[1]["role"] == "user"


class TestClaudeClient:
    def test_inherits_llm_client(self):
        from orchestrator.claude_client import ClaudeClient
        assert issubclass(ClaudeClient, LLMClient)

    def test_init_defaults(self):
        from orchestrator.claude_client import ClaudeClient
        client = ClaudeClient({"api_key": "sk-ant-test"})
        assert client._model == "claude-sonnet-4-20250514"

    def test_init_custom_model(self):
        from orchestrator.claude_client import ClaudeClient
        client = ClaudeClient({"api_key": "sk-ant-test", "model": "claude-opus-4-20250514"})
        assert client._model == "claude-opus-4-20250514"

    @patch("orchestrator.claude_client.anthropic.Anthropic")
    def test_chat_error_handling(self, mock_anthropic_cls):
        from orchestrator.claude_client import ClaudeClient
        mock_instance = MagicMock()
        mock_anthropic_cls.return_value = mock_instance
        mock_instance.messages.create.side_effect = Exception("Auth failed")

        client = ClaudeClient({"api_key": "test"})
        client._client = mock_instance
        resp = client.chat([{"role": "user", "content": "test"}])
        assert resp.stop_reason == "error"
        assert "Auth failed" in resp.text

    def test_format_tool_results(self):
        from orchestrator.claude_client import ClaudeClient
        client = ClaudeClient({"api_key": "test"})
        assistant_msg = {"role": "assistant", "content": "some content"}
        results = [ToolResultMessage(tool_call_id="toolu_123", content='{"status": "success"}')]
        messages = client.format_tool_results(assistant_msg, results)
        assert len(messages) == 2
        assert messages[0] == assistant_msg
        assert messages[1]["role"] == "user"
        assert messages[1]["content"][0]["type"] == "tool_result"
        assert messages[1]["content"][0]["tool_use_id"] == "toolu_123"


class TestOrchestratorWithMockLLM:
    """Orchestrator가 LLMClient 인터페이스를 올바르게 사용하는지 테스트."""

    def test_orchestrator_accepts_llm_client(self):
        """llm_client 파라미터로 커스텀 클라이언트를 주입할 수 있다."""
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.chat.return_value = LLMResponse(text="Hello", stop_reason="end_turn")

        from orchestrator.core import Orchestrator
        orch = Orchestrator(config={"tools": {}}, llm_client=mock_llm)
        assert orch.llm is mock_llm

    def test_orchestrator_run_simple(self):
        """단순 텍스트 응답 흐름."""
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.chat.return_value = LLMResponse(text="설계를 도와드리겠습니다.", stop_reason="end_turn")

        from orchestrator.core import Orchestrator
        orch = Orchestrator(config={"tools": {}}, llm_client=mock_llm)
        result = orch.run("브래킷 설계해줘")

        assert result == "설계를 도와드리겠습니다."
        assert mock_llm.chat.call_count == 1

    def test_orchestrator_run_with_tool_use(self):
        """tool_use → 실행 → 결과 반환 흐름."""
        mock_llm = MagicMock(spec=LLMClient)

        # 1차: tool_use 응답
        tool_response = LLMResponse(
            text="",
            tool_calls=[ToolCall(id="call_1", name="unknown__action", input={})],
            stop_reason="tool_use",
        )
        # 2차: 최종 텍스트 응답
        final_response = LLMResponse(text="완료했습니다.", stop_reason="end_turn")
        mock_llm.chat.side_effect = [tool_response, final_response]
        mock_llm.format_tool_results.return_value = [
            {"role": "assistant", "content": "tool call"},
            {"role": "user", "content": "tool result"},
        ]

        from orchestrator.core import Orchestrator
        orch = Orchestrator(config={"tools": {}}, llm_client=mock_llm)
        result = orch.run("뭔가 해줘")

        assert result == "완료했습니다."
        assert mock_llm.chat.call_count == 2
        assert mock_llm.format_tool_results.call_count == 1

    def test_orchestrator_error_response(self):
        """LLM 에러 시 에러 메시지 반환."""
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.chat.return_value = LLMResponse(text="API 호출 실패", stop_reason="error")

        from orchestrator.core import Orchestrator
        orch = Orchestrator(config={"tools": {}}, llm_client=mock_llm)
        result = orch.run("테스트")

        assert "실패" in result


class TestConfigLLMSelection:
    """config에서 LLM 프로바이더 선택이 올바르게 동작하는지 테스트."""

    def test_config_has_llm_section(self):
        from config.loader import load_config
        config = load_config()
        assert "llm" in config
        assert "provider" in config["llm"]

    def test_config_default_provider(self):
        from config.loader import load_config
        config = load_config()
        # tools.yaml에 gemini로 설정됨
        assert config["llm"]["provider"] == "gemini"

    def test_config_model_loaded(self):
        from config.loader import load_config
        config = load_config()
        assert config["llm"]["model"]  # 비어있지 않음
