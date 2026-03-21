"""설계 계획 수립 및 승인 흐름."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlanStatus(Enum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


@dataclass
class PlanStep:
    order: int
    tool: str
    action: str
    parameters: dict
    description: str
    validation_items: list[str] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)


@dataclass
class DesignPlan:
    plan_id: str
    task_type: str
    summary: str
    steps: list[PlanStep] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    user_notes: str = ""

    def to_display(self) -> str:
        """사용자에게 보여줄 계획 요약을 생성한다."""
        lines = [
            f"## 설계 계획: {self.summary}",
            f"작업 유형: {self.task_type}",
            "",
            "### 실행 단계",
        ]
        for step in self.steps:
            deps = f" (선행: {step.depends_on})" if step.depends_on else ""
            lines.append(f"{step.order}. [{step.tool}] {step.description}{deps}")
            for v in step.validation_items:
                lines.append(f"   - 검증: {v}")
        if self.tradeoffs:
            lines.append("")
            lines.append("### 트레이드오프")
            for t in self.tradeoffs:
                lines.append(f"- {t}")
        return "\n".join(lines)


class Planner:
    """설계 계획을 수립하고 사용자 승인을 관리한다."""

    def __init__(self) -> None:
        self._plans: dict[str, DesignPlan] = {}
        self._counter = 0

    def create_plan(
        self,
        task_type: str,
        summary: str,
        steps: list[dict[str, Any]],
        tradeoffs: list[str] | None = None,
    ) -> DesignPlan:
        """새 설계 계획을 생성한다."""
        self._counter += 1
        plan_id = f"plan_{self._counter:04d}"

        plan_steps = [
            PlanStep(
                order=s.get("order", i + 1),
                tool=s["tool"],
                action=s["action"],
                parameters=s.get("parameters", {}),
                description=s["description"],
                validation_items=s.get("validation_items", []),
                depends_on=s.get("depends_on", []),
            )
            for i, s in enumerate(steps)
        ]

        plan = DesignPlan(
            plan_id=plan_id,
            task_type=task_type,
            summary=summary,
            steps=plan_steps,
            tradeoffs=tradeoffs or [],
            status=PlanStatus.AWAITING_APPROVAL,
        )
        self._plans[plan_id] = plan
        return plan

    def approve(self, plan_id: str) -> DesignPlan:
        """계획을 승인한다."""
        plan = self._plans[plan_id]
        plan.status = PlanStatus.APPROVED
        return plan

    def reject(self, plan_id: str, notes: str = "") -> DesignPlan:
        """계획을 거부한다."""
        plan = self._plans[plan_id]
        plan.status = PlanStatus.REJECTED
        plan.user_notes = notes
        return plan

    def get_plan(self, plan_id: str) -> DesignPlan | None:
        return self._plans.get(plan_id)

    def get_approved_steps(self, plan_id: str) -> list[PlanStep]:
        """승인된 계획의 실행 단계를 반환한다."""
        plan = self._plans.get(plan_id)
        if plan and plan.status == PlanStatus.APPROVED:
            return plan.steps
        return []
