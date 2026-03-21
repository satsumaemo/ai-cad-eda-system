"""설계 명세 데이터 구조 — CAD 모델과 동기화되는 표준화된 중간 계층.

DesignSpec은 LLM 프로바이더에 독립적인 표준 형식으로,
어떤 LLM(Gemini, Claude 등)이든 같은 형식으로 입출력한다.
이를 통해 모델 교체 시에도 파이프라인이 그대로 동작한다.

구조:
    - parameters: 파라미터 테이블 (이름 → DesignParameter)
    - constraints: 제약 조건 목록
    - objectives: 목적 함수 목록
    - history: 변경 이력 (DesignDelta 리스트)
"""

import json
import logging
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DesignParameter:
    """설계 파라미터 하나를 표현한다."""

    name: str
    value: float | str
    unit: str = ""
    expression: str = ""
    min_value: float | None = None
    max_value: float | None = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}


@dataclass
class DesignConstraint:
    """설계 제약 조건."""

    name: str
    expression: str  # 예: "width >= 10", "thickness <= 50"
    type: str = "inequality"  # "inequality", "equality", "range"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DesignObjective:
    """최적화 목적 함수."""

    name: str
    target: str  # "minimize", "maximize", "target"
    expression: str  # 예: "mass", "safety_factor"
    target_value: float | None = None
    weight: float = 1.0
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}


@dataclass
class DesignDelta:
    """파라미터 변경 이력 레코드."""

    timestamp: float
    parameter_name: str
    old_value: float | str | None
    new_value: float | str
    source: str = ""  # "user", "optimizer", "llm", "fusion_event"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DesignSpec:
    """설계 명세 — 파라미터 테이블, 제약 조건, 목적 함수, 변경 이력을 관리한다.

    표준화된 중간 계층으로서 어떤 LLM이든 같은 형식으로 입출력한다.
    JSON 직렬화/역직렬화를 지원하며, 파라미터 변경 시 delta를 자동 기록한다.
    """

    def __init__(self, name: str = "", description: str = "") -> None:
        self.name: str = name
        self.description: str = description
        self.parameters: dict[str, DesignParameter] = {}
        self.constraints: list[DesignConstraint] = []
        self.objectives: list[DesignObjective] = []
        self.history: list[DesignDelta] = []
        self.metadata: dict[str, Any] = {}
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

    # ─── 파라미터 관리 ───

    def set_parameter(
        self,
        name: str,
        value: float | str,
        unit: str = "",
        expression: str = "",
        min_value: float | None = None,
        max_value: float | None = None,
        description: str = "",
        source: str = "",
    ) -> None:
        """파라미터를 설정하거나 업데이트한다. 변경 시 delta를 자동 기록한다."""
        old_value: float | str | None = None

        if name in self.parameters:
            old_param = self.parameters[name]
            old_value = old_param.value
            # 값이 같으면 무시
            if old_value == value:
                return

        self.parameters[name] = DesignParameter(
            name=name,
            value=value,
            unit=unit,
            expression=expression,
            min_value=min_value,
            max_value=max_value,
            description=description,
        )

        # delta 기록
        self.history.append(DesignDelta(
            timestamp=time.time(),
            parameter_name=name,
            old_value=old_value,
            new_value=value,
            source=source,
        ))

        self.updated_at = time.time()
        logger.debug("DesignSpec parameter '%s' updated: %s → %s (source=%s)", name, old_value, value, source)

    def get_parameter(self, name: str) -> DesignParameter | None:
        """파라미터를 이름으로 조회한다."""
        return self.parameters.get(name)

    def remove_parameter(self, name: str) -> bool:
        """파라미터를 제거한다."""
        if name in self.parameters:
            del self.parameters[name]
            self.updated_at = time.time()
            return True
        return False

    # ─── 제약 조건 ───

    def add_constraint(
        self,
        name: str,
        expression: str,
        constraint_type: str = "inequality",
        description: str = "",
    ) -> None:
        """제약 조건을 추가한다."""
        self.constraints.append(DesignConstraint(
            name=name,
            expression=expression,
            type=constraint_type,
            description=description,
        ))
        self.updated_at = time.time()

    # ─── 목적 함수 ───

    def add_objective(
        self,
        name: str,
        target: str,
        expression: str,
        target_value: float | None = None,
        weight: float = 1.0,
        description: str = "",
    ) -> None:
        """목적 함수를 추가한다."""
        self.objectives.append(DesignObjective(
            name=name,
            target=target,
            expression=expression,
            target_value=target_value,
            weight=weight,
            description=description,
        ))
        self.updated_at = time.time()

    # ─── 직렬화 ───

    def to_dict(self) -> dict[str, Any]:
        """JSON 호환 딕셔너리로 변환한다."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                name: param.to_dict() for name, param in self.parameters.items()
            },
            "constraints": [c.to_dict() for c in self.constraints],
            "objectives": [o.to_dict() for o in self.objectives],
            "history": [d.to_dict() for d in self.history],
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self, indent: int = 2) -> str:
        """JSON 문자열로 직렬화한다."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, path: str | Path) -> None:
        """파일에 저장한다."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())
        logger.info("DesignSpec saved to %s", path)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DesignSpec":
        """딕셔너리에서 DesignSpec을 복원한다."""
        spec = cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
        )

        for name, param_data in data.get("parameters", {}).items():
            spec.parameters[name] = DesignParameter(
                name=param_data.get("name", name),
                value=param_data.get("value", 0),
                unit=param_data.get("unit", ""),
                expression=param_data.get("expression", ""),
                min_value=param_data.get("min_value"),
                max_value=param_data.get("max_value"),
                description=param_data.get("description", ""),
            )

        for c_data in data.get("constraints", []):
            spec.constraints.append(DesignConstraint(
                name=c_data.get("name", ""),
                expression=c_data.get("expression", ""),
                type=c_data.get("type", "inequality"),
                description=c_data.get("description", ""),
            ))

        for o_data in data.get("objectives", []):
            spec.objectives.append(DesignObjective(
                name=o_data.get("name", ""),
                target=o_data.get("target", "minimize"),
                expression=o_data.get("expression", ""),
                target_value=o_data.get("target_value"),
                weight=o_data.get("weight", 1.0),
                description=o_data.get("description", ""),
            ))

        for h_data in data.get("history", []):
            spec.history.append(DesignDelta(
                timestamp=h_data.get("timestamp", 0),
                parameter_name=h_data.get("parameter_name", ""),
                old_value=h_data.get("old_value"),
                new_value=h_data.get("new_value", ""),
                source=h_data.get("source", ""),
            ))

        spec.metadata = data.get("metadata", {})
        spec.created_at = data.get("created_at", time.time())
        spec.updated_at = data.get("updated_at", time.time())

        return spec

    @classmethod
    def from_json(cls, json_str: str) -> "DesignSpec":
        """JSON 문자열에서 DesignSpec을 복원한다."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def load(cls, path: str | Path) -> "DesignSpec":
        """파일에서 로드한다."""
        with open(path, encoding="utf-8") as f:
            return cls.from_json(f.read())

    # ─── LLM 전달용 ───

    def to_llm_context(self) -> str:
        """LLM에 전달할 텍스트 형식으로 변환한다.

        어떤 LLM 프로바이더든 동일한 형식으로 입력받을 수 있도록
        표준화된 텍스트를 생성한다.
        """
        lines: list[str] = []
        lines.append(f"# Design Specification: {self.name}")
        if self.description:
            lines.append(f"Description: {self.description}")
        lines.append("")

        if self.parameters:
            lines.append("## Parameters")
            for name, param in self.parameters.items():
                range_str = ""
                if param.min_value is not None or param.max_value is not None:
                    lo = param.min_value if param.min_value is not None else "-inf"
                    hi = param.max_value if param.max_value is not None else "+inf"
                    range_str = f" [{lo}, {hi}]"
                unit_str = f" {param.unit}" if param.unit else ""
                lines.append(f"- {name}: {param.value}{unit_str}{range_str}")
            lines.append("")

        if self.constraints:
            lines.append("## Constraints")
            for c in self.constraints:
                lines.append(f"- {c.name}: {c.expression}")
            lines.append("")

        if self.objectives:
            lines.append("## Objectives")
            for o in self.objectives:
                target_str = f" (target={o.target_value})" if o.target_value is not None else ""
                lines.append(f"- {o.target} {o.expression}{target_str}")
            lines.append("")

        return "\n".join(lines)

    def get_recent_changes(self, n: int = 10) -> list[DesignDelta]:
        """최근 n개의 변경 이력을 반환한다."""
        return sorted(self.history, key=lambda d: d.timestamp, reverse=True)[:n]

    def __repr__(self) -> str:
        return (
            f"DesignSpec(name={self.name!r}, "
            f"params={len(self.parameters)}, "
            f"constraints={len(self.constraints)}, "
            f"objectives={len(self.objectives)}, "
            f"history={len(self.history)})"
        )
