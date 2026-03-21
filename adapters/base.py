"""어댑터 추상 클래스 — 모든 도구 어댑터가 반드시 이 인터페이스를 구현한다."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import logging
import time

logger = logging.getLogger(__name__)


class Status(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    WARNING = "warning"


@dataclass
class ValidationCheck:
    name: str
    status: Status
    value: Any
    threshold: Any = None
    message: str = ""


@dataclass
class ToolResult:
    tool: str
    status: Status
    result: dict
    validation: list[ValidationCheck] = field(default_factory=list)
    error: str | None = None
    elapsed_seconds: float = 0.0

    def to_summary(self) -> dict:
        """Claude API에 전달할 요약 딕셔너리를 반환한다."""
        return {
            "tool": self.tool,
            "status": self.status.value,
            "result": self.result,
            "validation": [
                {
                    "name": v.name,
                    "status": v.status.value,
                    "value": v.value,
                    "threshold": v.threshold,
                    "message": v.message,
                }
                for v in self.validation
            ],
            "error": self.error,
            "elapsed_seconds": self.elapsed_seconds,
        }

    @property
    def all_pass(self) -> bool:
        return all(v.status == Status.SUCCESS for v in self.validation)

    @property
    def has_failure(self) -> bool:
        return any(v.status == Status.FAILURE for v in self.validation)


class BaseAdapter(ABC):
    """모든 도구 어댑터의 추상 기반 클래스."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @abstractmethod
    def execute(self, action: str, parameters: dict, context: dict) -> ToolResult:
        """명령을 실행하고 결과를 반환한다.

        Args:
            action: 수행할 작업 이름 (예: "create_sketch", "run_simulation")
            parameters: 작업 파라미터 딕셔너리
            context: 파이프라인 상태, 이전 결과 등 맥락 정보

        Returns:
            ToolResult: 실행 결과 및 검증 정보
        """
        pass

    @abstractmethod
    def validate_result(self, result: ToolResult) -> ToolResult:
        """결과에 대한 검증을 수행한다.

        Args:
            result: 검증할 ToolResult

        Returns:
            ToolResult: 검증 결과가 추가된 ToolResult
        """
        pass

    @abstractmethod
    def get_capabilities(self) -> dict:
        """이 도구가 수행 가능한 작업 목록을 반환한다.

        Returns:
            dict: 작업 이름 → {"description": str, "parameters_schema": dict} 매핑
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """도구가 정상 동작하는지 확인한다.

        Returns:
            bool: 정상이면 True
        """
        pass

    def execute_with_timing(self, action: str, parameters: dict, context: dict) -> ToolResult:
        """execute()를 호출하고 소요 시간을 기록한다."""
        start = time.monotonic()
        try:
            result = self.execute(action, parameters, context)
        except Exception as e:
            self._logger.error("Adapter execution failed: %s", e, exc_info=True)
            result = ToolResult(
                tool=self.__class__.__name__,
                status=Status.FAILURE,
                result={},
                error=str(e),
            )
        result.elapsed_seconds = time.monotonic() - start
        return result

    def _make_result(
        self,
        status: Status,
        result: dict,
        validation: list[ValidationCheck] | None = None,
        error: str | None = None,
    ) -> ToolResult:
        """ToolResult 생성 헬퍼."""
        return ToolResult(
            tool=self.__class__.__name__,
            status=status,
            result=result,
            validation=validation or [],
            error=error,
        )
