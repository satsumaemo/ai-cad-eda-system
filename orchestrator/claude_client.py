"""Claude LLM 클라이언트 — anthropic SDK 사용."""

import json
import logging
import os
from typing import Any

import anthropic

from orchestrator.llm_client import LLMClient, LLMResponse, ToolCall, ToolResultMessage

logger = logging.getLogger(__name__)


class ClaudeClient(LLMClient):
    """Anthropic Claude API 클라이언트."""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        api_key = config.get("api_key") or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = config.get("model", os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"))
        self._max_tokens = config.get("max_tokens", 8192)

        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def chat(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict] | None = None,
        tool_config_mode: str = "AUTO",
    ) -> LLMResponse:
        """Claude API로 메시지를 보내고 응답을 받는다."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as e:
            logger.error("Claude API call failed: %s", e, exc_info=True)
            return LLMResponse(
                text=f"Claude API 호출 실패: {e}",
                stop_reason="error",
            )

        return self._parse_response(response)

    def format_tool_results(
        self,
        assistant_message: dict[str, Any],
        tool_results: list[ToolResultMessage],
    ) -> list[dict[str, Any]]:
        """도구 결과를 Claude 대화 형식으로 변환한다."""
        messages: list[dict[str, Any]] = []

        # assistant의 tool_use 응답
        messages.append(assistant_message)

        # tool_result를 user 메시지로
        result_blocks: list[dict[str, Any]] = []
        for tr in tool_results:
            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tr.tool_call_id,
                "content": tr.content,
            })

        messages.append({"role": "user", "content": result_blocks})
        return messages

    # ─── 내부 메서드 ───

    def _parse_response(self, response) -> LLMResponse:
        """Claude 응답을 LLMResponse로 변환한다."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        stop_reason = response.stop_reason or "end_turn"

        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw=response,
        )
