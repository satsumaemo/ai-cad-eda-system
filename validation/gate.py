"""단계별 검증 게이트 — 통과해야만 다음 단계로 진행.

두 가지 모드 지원:
1. operator 기반 판정 (pass_fail_criteria.yaml의 threshold/operator 형식)
2. 범위 기반 판정 (min/max, expected, target/tolerance 형식)
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from adapters.base import Status, ToolResult, ValidationCheck

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    """게이트 판정 결과."""
    overall_status: Status
    checks: list[ValidationCheck]
    passed: bool
    failed_items: list[ValidationCheck] = field(default_factory=list)
    warning_items: list[ValidationCheck] = field(default_factory=list)


class ValidationGate:
    """파이프라인 단계 간 검증 게이트."""

    @staticmethod
    def normalize_criteria(criteria: dict) -> dict:
        """pass_fail_criteria.yaml 형식을 operator 기반으로 변환한다.

        입력 형식: {"safety_factor": {"default": 2.0, "min_pass": 1.5, "warning": 1.2, "fail": 1.0}}
        출력 형식: {"safety_factor": {"operator": ">=", "threshold": 1.5, "warning_threshold": 1.2}}
        """
        normalized: dict = {}
        for name, spec in criteria.items():
            if not isinstance(spec, dict):
                continue
            if "operator" in spec or "min" in spec or "max" in spec or "expected" in spec:
                # 이미 호환 형식
                normalized[name] = spec
            elif "min_pass" in spec or "fail" in spec:
                # structural 형식 → operator 형식 (값이 클수록 좋음)
                normalized[name] = {
                    "operator": ">=",
                    "threshold": spec.get("min_pass", spec.get("default", 0)),
                    "warning_threshold": spec.get("warning", spec.get("min_pass", 0)),
                }
            elif "pass" in spec and "fail" in spec:
                # mesh/cfd 형식 (pass < warning < fail → 값이 작을수록 좋음)
                if spec["pass"] < spec["fail"]:
                    normalized[name] = {
                        "operator": "<=",
                        "threshold": spec["pass"],
                        "warning_threshold": spec.get("warning", spec["fail"]),
                    }
                else:
                    # 값이 클수록 좋음
                    normalized[name] = {
                        "operator": ">=",
                        "threshold": spec["pass"],
                        "warning_threshold": spec.get("warning", spec["fail"]),
                    }
        return normalized

    def check_stage(self, result_data: dict, criteria: dict) -> GateResult:
        """결과 데이터를 단계별 판정 기준과 대조하여 판정한다.

        criteria 형식 (operator 기반):
            {"safety_factor": {"threshold": 2.0, "operator": ">=", "warning_threshold": 1.5}}

        Args:
            result_data: 시뮬레이션 결과 딕셔너리
            criteria: 단계별 판정 기준

        Returns:
            GateResult
        """
        checks: list[ValidationCheck] = []

        for metric_name, criterion in criteria.items():
            value = result_data.get(metric_name)
            if value is None:
                checks.append(ValidationCheck(
                    name=metric_name,
                    status=Status.WARNING,
                    value=None,
                    threshold=criterion,
                    message=f"결과에 {metric_name} 없음",
                ))
                continue

            if "operator" in criterion:
                check = self._evaluate_operator(metric_name, value, criterion)
            else:
                check = self._evaluate_criterion(metric_name, value, criterion)
            checks.append(check)

        failed = [c for c in checks if c.status == Status.FAILURE]
        warnings = [c for c in checks if c.status == Status.WARNING]
        overall = (
            Status.FAILURE if failed else
            Status.WARNING if warnings else
            Status.SUCCESS
        )

        return GateResult(
            overall_status=overall,
            checks=checks,
            passed=len(failed) == 0,
            failed_items=failed,
            warning_items=warnings,
        )

    def check(self, result: ToolResult, criteria: dict) -> list[ValidationCheck]:
        """ToolResult를 기준과 비교하여 추가 검증을 수행한다.

        기존 인터페이스 호환용.
        """
        checks: list[ValidationCheck] = []

        if result.status == Status.FAILURE:
            checks.append(ValidationCheck(
                name="tool_execution",
                status=Status.FAILURE,
                value=result.error,
                message=f"도구 실행 실패: {result.error}",
            ))
            return checks

        for check_name, check_spec in criteria.items():
            actual_value = result.result.get(check_name)
            if actual_value is None:
                checks.append(ValidationCheck(
                    name=check_name,
                    status=Status.WARNING,
                    value=None,
                    message=f"{check_name}: 결과에 해당 값 없음",
                ))
                continue

            if "operator" in check_spec:
                check = self._evaluate_operator(check_name, actual_value, check_spec)
            else:
                check = self._evaluate_criterion(check_name, actual_value, check_spec)
            checks.append(check)

        return checks

    def can_proceed(self, result: ToolResult) -> bool:
        """다음 단계로 진행 가능한지 판단한다."""
        return not result.has_failure

    # ─── operator 기반 판정 ───

    def _evaluate_operator(
        self, name: str, value: Any, criterion: dict
    ) -> ValidationCheck:
        """operator/threshold 기반으로 판정한다."""
        op = criterion.get("operator", ">=")
        threshold = criterion.get("threshold")
        warning_threshold = criterion.get("warning_threshold")
        description = criterion.get("description", "")

        if threshold is None:
            return ValidationCheck(
                name=name, status=Status.SUCCESS, value=value,
                message=f"{name} = {value} (threshold 미정의)",
            )

        passed = self._compare(value, op, threshold)
        if passed:
            return ValidationCheck(
                name=name, status=Status.SUCCESS, value=value,
                threshold=threshold,
                message=description or f"{name} = {value} {op} {threshold} (PASS)",
            )

        # warning 체크
        if warning_threshold is not None:
            warning_passed = self._compare(value, op, warning_threshold)
            if warning_passed:
                return ValidationCheck(
                    name=name, status=Status.WARNING, value=value,
                    threshold=threshold,
                    message=description or f"{name} = {value} (WARNING, {op} {warning_threshold})",
                )

        return ValidationCheck(
            name=name, status=Status.FAILURE, value=value,
            threshold=threshold,
            message=description or f"{name} = {value}, 기준 {op} {threshold} 미달 (FAIL)",
        )

    @staticmethod
    def _compare(value: Any, op: str, threshold: Any) -> bool:
        """비교 연산을 수행한다."""
        if op == ">=":
            return value >= threshold
        if op == "<=":
            return value <= threshold
        if op == ">":
            return value > threshold
        if op == "<":
            return value < threshold
        if op == "==":
            return value == threshold
        if op == "!=":
            return value != threshold
        return False

    # ─── 범위 기반 판정 (기존 호환) ───

    def _evaluate_criterion(
        self, name: str, value: Any, spec: dict
    ) -> ValidationCheck:
        """범위/정확값/tolerance 기반 판정."""
        # min/max 범위 검사
        if "min" in spec or "max" in spec:
            min_val = spec.get("min", float("-inf"))
            max_val = spec.get("max", float("inf"))
            if min_val <= value <= max_val:
                return ValidationCheck(
                    name=name, status=Status.SUCCESS, value=value,
                    threshold=spec, message=f"{name} = {value} (범위 내)",
                )
            return ValidationCheck(
                name=name, status=Status.FAILURE, value=value,
                threshold=spec, message=f"{name} = {value} (범위 [{min_val}, {max_val}] 초과)",
            )

        # 정확한 값 비교
        if "expected" in spec:
            if value == spec["expected"]:
                return ValidationCheck(
                    name=name, status=Status.SUCCESS, value=value,
                    threshold=spec, message=f"{name} = {value} (기대값 일치)",
                )
            return ValidationCheck(
                name=name, status=Status.FAILURE, value=value,
                threshold=spec, message=f"{name} = {value} != {spec['expected']}",
            )

        # tolerance 기반 비교
        if "target" in spec and "tolerance" in spec:
            target = spec["target"]
            tol = spec["tolerance"]
            deviation = abs(value - target) / abs(target) if target != 0 else abs(value)
            if deviation <= tol:
                return ValidationCheck(
                    name=name, status=Status.SUCCESS, value=value,
                    threshold=spec, message=f"{name} = {value}, 편차 {deviation:.4f} <= {tol}",
                )
            return ValidationCheck(
                name=name, status=Status.FAILURE, value=value,
                threshold=spec, message=f"{name} = {value}, 편차 {deviation:.4f} > {tol}",
            )

        return ValidationCheck(
            name=name, status=Status.SUCCESS, value=value,
            message=f"{name} = {value} (기준 형식 미인식, 기록만)",
        )
