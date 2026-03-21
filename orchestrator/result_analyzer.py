"""시뮬레이션 결과 분석 및 pass/fail 판정."""

import logging
from typing import Any

from adapters.base import Status, ValidationCheck

logger = logging.getLogger(__name__)


class ResultAnalyzer:
    """시뮬레이션 결과를 수치 기반으로 분석하고 pass/fail/warning을 판정한다."""

    def __init__(self, criteria: dict | None = None) -> None:
        if criteria is not None:
            self.criteria = criteria
        else:
            from validation.criteria_loader import CriteriaLoader
            self.criteria = CriteriaLoader().raw

    def analyze(self, domain: str, metrics: dict[str, Any]) -> list[ValidationCheck]:
        """도메인별 기준에 따라 메트릭을 판정한다.

        Args:
            domain: 판정 도메인 (geometry, mesh, structural, thermal, cfd, electromagnetic, pcb)
            metrics: 검사할 메트릭 딕셔너리 (키: 항목명, 값: 측정값)

        Returns:
            ValidationCheck 리스트
        """
        domain_criteria = self.criteria.get(domain, {})
        checks: list[ValidationCheck] = []

        for metric_name, measured_value in metrics.items():
            criterion = domain_criteria.get(metric_name)
            if criterion is None:
                logger.warning("No criterion for %s.%s, skipping", domain, metric_name)
                continue

            check = self._evaluate(metric_name, measured_value, criterion)
            checks.append(check)

        return checks

    def _evaluate(
        self, name: str, value: Any, criterion: dict
    ) -> ValidationCheck:
        """단일 메트릭을 기준과 비교하여 판정한다."""
        # pass/warning/fail 임계값 기반 판정
        if "pass" in criterion and "fail" in criterion:
            return self._threshold_check(name, value, criterion)

        # 절대값 비교 (예: DRC violations = 0)
        if "max" in criterion or "min" in criterion:
            return self._range_check(name, value, criterion)

        # 기본: 값만 기록
        return ValidationCheck(
            name=name,
            status=Status.SUCCESS,
            value=value,
            message=f"{name} = {value} (기준 없음, 기록만)",
        )

    def _threshold_check(
        self, name: str, value: float, criterion: dict
    ) -> ValidationCheck:
        """pass/warning/fail 임계값으로 판정한다."""
        pass_threshold = criterion["pass"]
        warning_threshold = criterion.get("warning", criterion["fail"])
        fail_threshold = criterion["fail"]

        # 값이 작을수록 좋은 경우 (오차, skewness 등)
        if pass_threshold <= fail_threshold:
            if value <= pass_threshold:
                status = Status.SUCCESS
                msg = f"{name} = {value} <= {pass_threshold} (PASS)"
            elif value <= warning_threshold:
                status = Status.WARNING
                msg = f"{name} = {value} <= {warning_threshold} (WARNING)"
            else:
                status = Status.FAILURE
                msg = f"{name} = {value} > {fail_threshold} (FAIL)"
        # 값이 클수록 좋은 경우 (안전계수 등)
        else:
            if value >= pass_threshold:
                status = Status.SUCCESS
                msg = f"{name} = {value} >= {pass_threshold} (PASS)"
            elif value >= warning_threshold:
                status = Status.WARNING
                msg = f"{name} = {value} >= {warning_threshold} (WARNING)"
            else:
                status = Status.FAILURE
                msg = f"{name} = {value} < {fail_threshold} (FAIL)"

        return ValidationCheck(
            name=name,
            status=status,
            value=value,
            threshold={"pass": pass_threshold, "warning": warning_threshold, "fail": fail_threshold},
            message=msg,
        )

    def _range_check(
        self, name: str, value: float, criterion: dict
    ) -> ValidationCheck:
        """범위 기반 판정."""
        min_val = criterion.get("min", float("-inf"))
        max_val = criterion.get("max", float("inf"))

        if min_val <= value <= max_val:
            return ValidationCheck(
                name=name,
                status=Status.SUCCESS,
                value=value,
                threshold={"min": min_val, "max": max_val},
                message=f"{name} = {value}, 범위 [{min_val}, {max_val}] 내 (PASS)",
            )
        return ValidationCheck(
            name=name,
            status=Status.FAILURE,
            value=value,
            threshold={"min": min_val, "max": max_val},
            message=f"{name} = {value}, 범위 [{min_val}, {max_val}] 초과 (FAIL)",
        )

    def summarize(self, checks: list[ValidationCheck]) -> dict:
        """판정 결과를 요약한다."""
        total = len(checks)
        passed = sum(1 for c in checks if c.status == Status.SUCCESS)
        warnings = sum(1 for c in checks if c.status == Status.WARNING)
        failures = sum(1 for c in checks if c.status == Status.FAILURE)

        overall = Status.SUCCESS
        if failures > 0:
            overall = Status.FAILURE
        elif warnings > 0:
            overall = Status.WARNING

        return {
            "overall": overall.value,
            "total": total,
            "passed": passed,
            "warnings": warnings,
            "failures": failures,
            "failed_items": [
                {"name": c.name, "value": c.value, "threshold": c.threshold, "message": c.message}
                for c in checks
                if c.status == Status.FAILURE
            ],
            "warning_items": [
                {"name": c.name, "value": c.value, "threshold": c.threshold, "message": c.message}
                for c in checks
                if c.status == Status.WARNING
            ],
        }
