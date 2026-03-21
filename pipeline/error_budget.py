"""누적 오차 버짓 관리 — 허용 오차를 예산처럼 관리하여 소진 시 고정밀 재실행."""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ErrorEntry:
    step: str
    error_type: str
    magnitude: float
    description: str = ""


class ErrorBudget:
    """파이프라인 전체의 누적 오차를 예산으로 관리한다.

    각 단계(변환, 메시, 시뮬레이션)에서 발생하는 오차를 기록하고,
    총 허용 예산 대비 잔여 여유를 추적한다.
    """

    def __init__(self, total_budget: float = 0.05) -> None:
        """
        Args:
            total_budget: 총 허용 오차 예산 (예: 0.05 = 5%)
        """
        self.total_budget = total_budget
        self.entries: list[ErrorEntry] = []

    @property
    def consumed(self) -> float:
        """소비된 오차 예산."""
        return sum(e.magnitude for e in self.entries)

    @property
    def remaining(self) -> float:
        """잔여 오차 예산."""
        return max(0.0, self.total_budget - self.consumed)

    @property
    def exhausted(self) -> bool:
        """예산이 소진되었는지 여부."""
        return self.consumed >= self.total_budget

    def record(self, step: str, error_type: str, magnitude: float, description: str = "") -> None:
        """오차를 기록한다."""
        entry = ErrorEntry(step=step, error_type=error_type, magnitude=magnitude, description=description)
        self.entries.append(entry)
        logger.info(
            "Error budget: +%.4f (%s/%s), remaining=%.4f",
            magnitude,
            step,
            error_type,
            self.remaining,
        )

        if self.exhausted:
            logger.warning(
                "Error budget exhausted: consumed=%.4f, budget=%.4f",
                self.consumed,
                self.total_budget,
            )

    def check_allowance(self, required: float) -> bool:
        """주어진 오차를 추가로 허용할 수 있는지 확인한다."""
        return self.remaining >= required

    def to_dict(self) -> dict:
        return {
            "total_budget": self.total_budget,
            "consumed": self.consumed,
            "remaining": self.remaining,
            "exhausted": self.exhausted,
            "entries": [
                {
                    "step": e.step,
                    "error_type": e.error_type,
                    "magnitude": e.magnitude,
                    "description": e.description,
                }
                for e in self.entries
            ],
        }

    def suggest_refinement(self) -> dict | None:
        """버짓 소진 시 가장 큰 오차 단계에 대한 정밀도 향상 제안을 반환한다."""
        if not self.entries:
            return None
        worst = max(self.entries, key=lambda e: e.magnitude)
        return {
            "step": worst.step,
            "error_type": worst.error_type,
            "current_error": worst.magnitude,
            "suggestion": f"{worst.step} 단계의 메시 밀도를 2배로 높여 재실행",
        }

    def reset(self) -> None:
        """오차 기록을 초기화한다."""
        self.entries.clear()
