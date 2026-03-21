"""Gemini LLM 클라이언트 — google-genai SDK 사용.

google-genai SDK의 함수 호출(function calling)을 사용하여
오케스트레이터의 tool_use 흐름을 구현한다.

주요 변경: 승인 후 실행은 core.py의 direct execution mode가 처리하므로
이 클라이언트는 정보 수집 단계(AUTO 모드)에만 사용된다.
"""

import json
import logging
import os
from typing import Any

from google import genai
from google.genai import types

from orchestrator.llm_client import LLMClient, LLMResponse, ToolCall, ToolResultMessage

logger = logging.getLogger(__name__)


class GeminiClient(LLMClient):
    """Google Gemini API 클라이언트."""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self._api_key = config.get("api_key") or os.getenv("GOOGLE_API_KEY", "")
        self._model = config.get("model", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
        self._max_tokens = config.get("max_tokens", 8192)
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        """API 클라이언트를 lazy 초기화한다."""
        if self._client is None:
            if not self._api_key:
                raise ValueError("GOOGLE_API_KEY가 설정되지 않았습니다. .env 또는 환경변수를 확인하세요.")
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def chat(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict] | None = None,
        tool_config_mode: str = "AUTO",
    ) -> LLMResponse:
        """Gemini API로 메시지를 보내고 응답을 받는다."""
        gemini_contents = self._convert_messages(messages)
        gemini_tools = self._convert_tools(tools) if tools else None
        if gemini_tools:
            logger.debug(
                "Gemini tools: %d declarations in %d Tool objects (mode=%s)",
                sum(len(t.function_declarations or []) for t in gemini_tools),
                len(gemini_tools),
                tool_config_mode,
            )

        tool_config = None
        if gemini_tools:
            tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=tool_config_mode,
                ),
            )

        config = types.GenerateContentConfig(
            system_instruction=system if system else None,
            max_output_tokens=self._max_tokens,
            temperature=0.0,
            tools=gemini_tools,
            tool_config=tool_config,
        )

        try:
            response = self._get_client().models.generate_content(
                model=self._model,
                contents=gemini_contents,
                config=config,
            )
        except Exception as e:
            logger.error("Gemini API call failed: %s", e, exc_info=True)
            return LLMResponse(
                text=f"Gemini API 호출 실패: {e}",
                stop_reason="error",
            )

        return self._parse_response(response)

    def format_tool_results(
        self,
        assistant_message: dict[str, Any],
        tool_results: list[ToolResultMessage],
    ) -> list[dict[str, Any]]:
        """도구 결과를 Gemini 대화 형식으로 변환한다."""
        messages: list[dict[str, Any]] = []

        # assistant의 tool_use 응답
        messages.append(assistant_message)

        # 도구 결과를 user 역할의 function response로
        tool_parts: list[dict[str, Any]] = []
        for tr in tool_results:
            try:
                result_data = json.loads(tr.content)
            except (json.JSONDecodeError, TypeError):
                result_data = {"result": tr.content}
            # Gemini는 function_response.name에 실제 함수 이름이 필요
            func_name = tr.tool_name or tr.tool_call_id
            tool_parts.append({
                "function_response": {
                    "name": func_name,
                    "response": result_data,
                },
            })

        messages.append({"role": "user", "parts": tool_parts})
        return messages

    # ─── 내부 변환 메서드 ───

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[types.Content]:
        """오케스트레이터 메시지를 Gemini Content로 변환한다."""
        contents: list[types.Content] = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            # Gemini는 "user"와 "model" 역할만 사용
            gemini_role = "model" if role == "assistant" else "user"

            # 단순 텍스트 메시지
            if isinstance(content, str):
                contents.append(types.Content(
                    role=gemini_role,
                    parts=[types.Part.from_text(text=content)],
                ))
                continue

            # 리스트 형태 (tool_use 응답 또는 tool_result)
            if isinstance(content, list):
                parts: list[types.Part] = []
                for item in content:
                    if isinstance(item, dict):
                        # tool_result 메시지
                        if item.get("type") == "tool_result":
                            parts.append(types.Part.from_function_response(
                                name=item.get("_function_name", item.get("tool_use_id", "unknown")),
                                response=json.loads(item.get("content", "{}")),
                            ))
                        # text block
                        elif item.get("type") == "text":
                            parts.append(types.Part.from_text(text=item.get("text", "")))
                        # function_call block (assistant)
                        elif "function_call" in item:
                            fc = item["function_call"]
                            parts.append(types.Part.from_function_call(
                                name=fc["name"],
                                args=fc.get("args", {}),
                            ))
                        # function_response block (user)
                        elif "function_response" in item:
                            fr = item["function_response"]
                            parts.append(types.Part.from_function_response(
                                name=fr["name"],
                                response=fr.get("response", {}),
                            ))
                    elif hasattr(item, "type"):
                        # Anthropic ContentBlock 객체 → 텍스트/tool_use 변환
                        if item.type == "text":
                            parts.append(types.Part.from_text(text=item.text))
                        elif item.type == "tool_use":
                            parts.append(types.Part.from_function_call(
                                name=item.name,
                                args=item.input,
                            ))

                if parts:
                    contents.append(types.Content(role=gemini_role, parts=parts))
                continue

            # 그 외: 문자열로 변환
            contents.append(types.Content(
                role=gemini_role,
                parts=[types.Part.from_text(text=str(content))],
            ))

        return contents

    def _convert_tools(self, tools: list[dict]) -> list[types.Tool]:
        """오케스트레이터 tools를 Gemini function declarations로 변환한다.

        오케스트레이터 형식:
            {"name": "...", "description": "...", "input_schema": {...}}

        Gemini 형식:
            Tool(function_declarations=[FunctionDeclaration(name, description, parameters)])
        """
        declarations: list[types.FunctionDeclaration] = []
        for tool in tools:
            schema = tool.get("input_schema", {})
            # Gemini는 Schema 객체를 사용
            parameters = self._convert_schema(schema) if schema.get("properties") else None

            declarations.append(types.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=parameters,
            ))

        return [types.Tool(function_declarations=declarations)]

    def _convert_schema(self, schema: dict) -> dict:
        """JSON Schema를 Gemini 호환 형식으로 변환한다."""
        # Gemini는 OpenAPI 스타일 스키마를 수용
        result: dict[str, Any] = {"type": schema.get("type", "object").upper()}
        if "properties" in schema:
            result["properties"] = {}
            for prop_name, prop_spec in schema["properties"].items():
                result["properties"][prop_name] = self._convert_property(prop_spec)
        if "required" in schema:
            result["required"] = schema["required"]
        return result

    def _convert_property(self, prop: dict) -> dict:
        """개별 프로퍼티 스키마를 변환한다."""
        result: dict[str, Any] = {}
        prop_type = prop.get("type", "string")
        result["type"] = prop_type.upper()
        if "description" in prop:
            result["description"] = prop["description"]
        if "enum" in prop:
            result["enum"] = [str(v) for v in prop["enum"]]
        if prop_type == "array" and "items" in prop:
            result["items"] = self._convert_property(prop["items"])
        if prop_type == "object" and "properties" in prop:
            result["properties"] = {
                k: self._convert_property(v)
                for k, v in prop["properties"].items()
            }
        return result

    def _parse_response(self, response) -> LLMResponse:
        """Gemini 응답을 LLMResponse로 변환한다."""
        logger.debug("Gemini raw response: %s", response)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        call_counter = 0

        if not response.candidates:
            logger.warning("Gemini returned no candidates")
            return LLMResponse(text="", stop_reason="end_turn", raw=response)

        for candidate in response.candidates:
            content = getattr(candidate, "content", None)
            if content is None:
                logger.debug("Candidate has no content: %s", candidate)
                continue
            parts = getattr(content, "parts", None)
            if not parts:
                logger.debug("Candidate content has no parts: %s", content)
                continue
            for part in parts:
                if part.text:
                    text_parts.append(part.text)
                elif part.function_call:
                    fc = part.function_call
                    call_counter += 1
                    logger.info(
                        "Parsed function_call: %s(args=%s)",
                        fc.name,
                        dict(fc.args) if fc.args else {},
                    )
                    tool_calls.append(ToolCall(
                        id=f"call_{fc.name}_{call_counter}",
                        name=fc.name,
                        input=dict(fc.args) if fc.args else {},
                    ))

        stop_reason = "tool_use" if tool_calls else "end_turn"
        finish_reason = getattr(response.candidates[0], "finish_reason", None)
        finish_str = str(finish_reason) if finish_reason else ""
        if finish_str == "MAX_TOKENS":
            stop_reason = "max_tokens"
        elif "MALFORMED_FUNCTION_CALL" in finish_str:
            logger.warning("Gemini MALFORMED_FUNCTION_CALL detected (finish_reason=%s)", finish_str)
            stop_reason = "malformed_tool_call"
            tool_calls.clear()  # 깨진 tool_call 무시

        logger.info(
            "Gemini parsed: text_len=%d, tool_calls=%d, stop=%s, finish=%s",
            len("\n".join(text_parts)), len(tool_calls), stop_reason, finish_str,
        )

        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw=response,
        )
