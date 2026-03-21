"""메시 품질 검사 — aspect ratio, skewness, jacobian 등."""

import logging
from typing import Any

from adapters.base import Status, ValidationCheck

logger = logging.getLogger(__name__)

# 기본 품질 기준
DEFAULT_CRITERIA = {
    "aspect_ratio": {"pass": 3.0, "warning": 5.0, "fail": 10.0},
    "skewness": {"pass": 0.5, "warning": 0.75, "fail": 0.95},
    "min_jacobian": {"pass": 0.3, "warning": 0.1, "fail": 0.0},
}


class MeshQualityChecker:
    """Gmsh 메시의 품질을 검사한다."""

    def __init__(self, criteria: dict | None = None) -> None:
        self.criteria = criteria or DEFAULT_CRITERIA

    def check(self, mesh_stats: dict[str, Any]) -> list[ValidationCheck]:
        """메시 통계를 기준과 비교하여 판정한다.

        Args:
            mesh_stats: 메시 품질 통계
                - "max_aspect_ratio": float
                - "max_skewness": float
                - "min_jacobian": float
                - "element_count": int
                - "node_count": int

        Returns:
            ValidationCheck 리스트
        """
        checks: list[ValidationCheck] = []

        # Aspect ratio (낮을수록 좋음)
        if "max_aspect_ratio" in mesh_stats:
            checks.append(self._check_lower_is_better(
                "aspect_ratio",
                mesh_stats["max_aspect_ratio"],
                self.criteria.get("aspect_ratio", DEFAULT_CRITERIA["aspect_ratio"]),
            ))

        # Skewness (낮을수록 좋음)
        if "max_skewness" in mesh_stats:
            checks.append(self._check_lower_is_better(
                "skewness",
                mesh_stats["max_skewness"],
                self.criteria.get("skewness", DEFAULT_CRITERIA["skewness"]),
            ))

        # Minimum Jacobian (높을수록 좋음)
        if "min_jacobian" in mesh_stats:
            checks.append(self._check_higher_is_better(
                "min_jacobian",
                mesh_stats["min_jacobian"],
                self.criteria.get("min_jacobian", DEFAULT_CRITERIA["min_jacobian"]),
            ))

        return checks

    def _check_lower_is_better(
        self, name: str, value: float, criterion: dict
    ) -> ValidationCheck:
        """값이 낮을수록 좋은 메트릭 판정."""
        if value <= criterion["pass"]:
            status = Status.SUCCESS
            msg = f"{name} = {value:.3f} <= {criterion['pass']} (PASS)"
        elif value <= criterion["warning"]:
            status = Status.WARNING
            msg = f"{name} = {value:.3f} <= {criterion['warning']} (WARNING)"
        else:
            status = Status.FAILURE
            msg = f"{name} = {value:.3f} > {criterion['fail']} (FAIL)"

        return ValidationCheck(name=name, status=status, value=value, threshold=criterion, message=msg)

    def _check_higher_is_better(
        self, name: str, value: float, criterion: dict
    ) -> ValidationCheck:
        """값이 높을수록 좋은 메트릭 판정."""
        if value >= criterion["pass"]:
            status = Status.SUCCESS
            msg = f"{name} = {value:.3f} >= {criterion['pass']} (PASS)"
        elif value >= criterion["warning"]:
            status = Status.WARNING
            msg = f"{name} = {value:.3f} >= {criterion['warning']} (WARNING)"
        else:
            status = Status.FAILURE
            msg = f"{name} = {value:.3f} < {criterion['fail']} (FAIL)"

        return ValidationCheck(name=name, status=status, value=value, threshold=criterion, message=msg)
