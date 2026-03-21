"""작업 상태 관리 및 체크포인트."""

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass
class StepRecord:
    adapter: str
    action: str
    result: dict
    timestamp: float = field(default_factory=time.time)


class PipelineState:
    """파이프라인 실행 상태를 추적하고 체크포인트를 관리한다."""

    def __init__(self) -> None:
        self.task_id: str = ""
        self.task_type: str = ""
        self.status: TaskStatus = TaskStatus.PENDING
        self.current_step: int = 0
        self.current_iteration: int = 0
        self.steps: list[StepRecord] = []
        self.parameters: dict[str, Any] = {}
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

    def record_step(self, adapter: str, action: str, result: dict) -> None:
        """실행 단계를 기록한다."""
        self.steps.append(StepRecord(adapter=adapter, action=action, result=result))
        self.current_step = len(self.steps)
        self.updated_at = time.time()

    def advance_iteration(self) -> None:
        """반복 카운터를 증가시킨다."""
        self.current_iteration += 1
        self.updated_at = time.time()

    def set_status(self, status: TaskStatus) -> None:
        self.status = status
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "current_step": self.current_step,
            "current_iteration": self.current_iteration,
            "steps": [
                {
                    "adapter": s.adapter,
                    "action": s.action,
                    "result": s.result,
                    "timestamp": s.timestamp,
                }
                for s in self.steps
            ],
            "parameters": self.parameters,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def save_checkpoint(self, path: str = "data/logs/checkpoint.json") -> None:
        """현재 상태를 파일에 저장한다."""
        checkpoint_path = Path(path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info("Checkpoint saved: %s", path)

    @classmethod
    def load_checkpoint(cls, path: str = "data/logs/checkpoint.json") -> "PipelineState":
        """체크포인트에서 상태를 복원한다."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        state = cls()
        state.task_id = data.get("task_id", "")
        state.task_type = data.get("task_type", "")
        state.status = TaskStatus(data.get("status", "pending"))
        state.current_step = data.get("current_step", 0)
        state.current_iteration = data.get("current_iteration", 0)
        state.parameters = data.get("parameters", {})
        state.created_at = data.get("created_at", time.time())
        state.updated_at = data.get("updated_at", time.time())

        for s in data.get("steps", []):
            state.steps.append(
                StepRecord(
                    adapter=s["adapter"],
                    action=s["action"],
                    result=s["result"],
                    timestamp=s.get("timestamp", 0),
                )
            )

        logger.info("Checkpoint loaded: %s", path)
        return state

    # ─── 이력 및 재개 ───

    def get_history(self) -> list[tuple[int, StepRecord]]:
        """전체 단계 이력을 시간순으로 반환한다."""
        return list(enumerate(sorted(self.steps, key=lambda s: s.timestamp)))

    def can_resume(self) -> bool:
        """중단 후 재개 가능한지 확인한다."""
        return len(self.steps) > 0 and self.status in (TaskStatus.PAUSED, TaskStatus.FAILED)

    def get_resume_point(self) -> int:
        """마지막으로 성공한 단계 인덱스를 반환한다. 없으면 0."""
        for i in range(len(self.steps) - 1, -1, -1):
            step_status = self.steps[i].result.get("status", "")
            if step_status == "success":
                return i + 1
        return 0

    def save_stage(self, stage_name: str, data: dict) -> None:
        """이름 기반으로 단계 결과를 저장한다."""
        self.record_step(
            adapter=data.get("adapter", stage_name),
            action=data.get("action", stage_name),
            result=data,
        )
