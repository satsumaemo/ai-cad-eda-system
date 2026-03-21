"""검증 게이트 테스트."""

import pytest

from adapters.base import Status, ToolResult
from validation.gate import ValidationGate, GateResult


@pytest.fixture
def gate():
    return ValidationGate()


class TestOperatorBasedGate:
    """operator 기반 판정 테스트."""

    def test_gte_pass(self, gate):
        result = gate.check_stage(
            {"safety_factor": 2.5},
            {"safety_factor": {"threshold": 2.0, "operator": ">="}},
        )
        assert result.passed
        assert result.overall_status == Status.SUCCESS

    def test_gte_fail(self, gate):
        result = gate.check_stage(
            {"safety_factor": 0.8},
            {"safety_factor": {"threshold": 2.0, "operator": ">="}},
        )
        assert not result.passed
        assert result.overall_status == Status.FAILURE

    def test_lte_pass(self, gate):
        result = gate.check_stage(
            {"max_displacement_mm": 0.3},
            {"max_displacement_mm": {"threshold": 1.0, "operator": "<="}},
        )
        assert result.passed

    def test_lte_fail(self, gate):
        result = gate.check_stage(
            {"max_displacement_mm": 2.0},
            {"max_displacement_mm": {"threshold": 1.0, "operator": "<="}},
        )
        assert not result.passed

    def test_eq_pass(self, gate):
        result = gate.check_stage(
            {"convergence": True},
            {"convergence": {"threshold": True, "operator": "=="}},
        )
        assert result.passed

    def test_eq_fail(self, gate):
        result = gate.check_stage(
            {"convergence": False},
            {"convergence": {"threshold": True, "operator": "=="}},
        )
        assert not result.passed

    def test_warning_threshold(self, gate):
        result = gate.check_stage(
            {"safety_factor": 1.7},
            {"safety_factor": {"threshold": 2.0, "operator": ">=", "warning_threshold": 1.5}},
        )
        assert result.passed  # warning은 pass로 취급
        assert result.overall_status == Status.WARNING
        assert len(result.warning_items) == 1

    def test_missing_metric(self, gate):
        result = gate.check_stage(
            {},
            {"safety_factor": {"threshold": 2.0, "operator": ">="}},
        )
        assert result.overall_status == Status.WARNING

    def test_multiple_criteria(self, gate):
        result = gate.check_stage(
            {"safety_factor": 2.5, "max_displacement_mm": 0.3, "convergence": True},
            {
                "safety_factor": {"threshold": 2.0, "operator": ">="},
                "max_displacement_mm": {"threshold": 1.0, "operator": "<="},
                "convergence": {"threshold": True, "operator": "=="},
            },
        )
        assert result.passed
        assert len(result.checks) == 3
        assert all(c.status == Status.SUCCESS for c in result.checks)

    def test_partial_failure(self, gate):
        result = gate.check_stage(
            {"safety_factor": 0.8, "convergence": True},
            {
                "safety_factor": {"threshold": 2.0, "operator": ">="},
                "convergence": {"threshold": True, "operator": "=="},
            },
        )
        assert not result.passed
        assert len(result.failed_items) == 1


class TestRangeBasedGate:
    """범위 기반 판정 (기존 호환) 테스트."""

    def test_min_max_pass(self, gate):
        result = gate.check_stage(
            {"temperature": 50},
            {"temperature": {"min": 0, "max": 100}},
        )
        assert result.passed

    def test_min_max_fail(self, gate):
        result = gate.check_stage(
            {"temperature": 150},
            {"temperature": {"min": 0, "max": 100}},
        )
        assert not result.passed

    def test_expected_pass(self, gate):
        result = gate.check_stage(
            {"drc_errors": 0},
            {"drc_errors": {"expected": 0}},
        )
        assert result.passed

    def test_target_tolerance_pass(self, gate):
        result = gate.check_stage(
            {"impedance": 49.5},
            {"impedance": {"target": 50, "tolerance": 0.05}},
        )
        assert result.passed

    def test_target_tolerance_fail(self, gate):
        result = gate.check_stage(
            {"impedance": 30},
            {"impedance": {"target": 50, "tolerance": 0.05}},
        )
        assert not result.passed


class TestToolResultCheck:
    """ToolResult 기반 check() 테스트."""

    def test_check_tool_failure(self, gate):
        result = ToolResult(
            tool="test", status=Status.FAILURE, result={}, error="crash"
        )
        checks = gate.check(result, {"x": {"min": 0, "max": 10}})
        assert any(c.status == Status.FAILURE for c in checks)

    def test_check_with_operator(self, gate):
        result = ToolResult(
            tool="test", status=Status.SUCCESS,
            result={"safety_factor": 2.5}, validation=[],
        )
        checks = gate.check(result, {"safety_factor": {"threshold": 2.0, "operator": ">="}})
        assert checks[0].status == Status.SUCCESS

    def test_can_proceed_true(self, gate):
        result = ToolResult(
            tool="test", status=Status.SUCCESS, result={}, validation=[],
        )
        assert gate.can_proceed(result) is True

    def test_can_proceed_false(self, gate):
        result = ToolResult(
            tool="test", status=Status.SUCCESS, result={},
            validation=[
                pytest.importorskip("adapters.base").ValidationCheck(
                    "x", Status.FAILURE, 0, message="fail"
                ),
            ],
        )
        assert gate.can_proceed(result) is False
