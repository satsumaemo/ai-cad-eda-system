"""LLM 클라이언트 추상 인터페이스 — Claude/Gemini 등 LLM 프로바이더를 교체 가능하게 한다.

모든 LLM 클라이언트는 이 인터페이스를 구현한다.
오케스트레이터는 LLMClient만 알고, 내부 SDK 차이를 모른다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """LLM이 요청한 도구 호출."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    """LLM 응답 표준 포맷.

    프로바이더별 응답을 이 구조로 변환하여 오케스트레이터에 반환한다.
    """
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""  # "end_turn", "tool_use", "max_tokens" 등
    raw: Any = None  # 원본 응답 (디버깅용)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


@dataclass
class ToolResultMessage:
    """도구 실행 결과를 LLM에 돌려줄 때 사용."""
    tool_call_id: str
    content: str
    tool_name: str = ""  # Gemini function_response에 필요한 함수 이름


class LLMClient(ABC):
    """LLM 클라이언트 추상 인터페이스."""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict] | None = None,
        tool_config_mode: str = "AUTO",
    ) -> LLMResponse:
        """메시지를 보내고 응답을 받는다.

        Args:
            messages: 대화 이력 (role/content 딕셔너리 리스트)
            system: system prompt
            tools: 사용 가능한 도구 목록 (Claude tools 형식)
            tool_config_mode: Gemini tool_config 모드 ("AUTO" 또는 "ANY").
                "ANY"이면 Gemini가 반드시 함수 호출을 시도한다.

        Returns:
            LLMResponse: 표준화된 응답
        """
        ...

    @abstractmethod
    def format_tool_results(
        self,
        assistant_message: dict[str, Any],
        tool_results: list[ToolResultMessage],
    ) -> list[dict[str, Any]]:
        """도구 실행 결과를 해당 LLM의 대화 형식으로 변환한다.

        Args:
            assistant_message: LLM의 tool_use 응답 (대화 이력에 추가할 assistant 메시지)
            tool_results: 도구 실행 결과 목록

        Returns:
            대화 이력에 추가할 메시지 목록 (assistant + user/tool 응답)
        """
        ...


def create_llm_client(config: dict) -> LLMClient:
    """config에서 LLM 프로바이더를 읽고 해당 클라이언트를 생성한다.

    Args:
        config: 전체 설정 딕셔너리. llm.provider로 프로바이더 결정.

    Returns:
        LLMClient 구현체
    """
    llm_config = config.get("llm", {})
    provider = llm_config.get("provider", "gemini")

    if provider == "claude":
        from orchestrator.claude_client import ClaudeClient
        return ClaudeClient(llm_config)
    elif provider == "gemini":
        from orchestrator.gemini_client import GeminiClient
        return GeminiClient(llm_config)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}. 지원: 'gemini', 'claude'")
