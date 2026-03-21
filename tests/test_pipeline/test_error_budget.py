"""오차 예산 테스트."""

from pipeline.error_budget import ErrorBudget


class TestErrorBudget:
    def test_initial(self):
        budget = ErrorBudget(total_budget=0.05)
        assert budget.remaining == 0.05
        assert budget.exhausted is False

    def test_record(self):
        budget = ErrorBudget(total_budget=0.05)
        budget.record("mesh", "geometry", 0.02)
        assert abs(budget.consumed - 0.02) < 1e-10
        assert abs(budget.remaining - 0.03) < 1e-10

    def test_exhausted(self):
        budget = ErrorBudget(total_budget=0.05)
        budget.record("step1", "a", 0.03)
        budget.record("step2", "b", 0.03)
        assert budget.exhausted is True
        assert budget.remaining == 0.0

    def test_check_allowance(self):
        budget = ErrorBudget(total_budget=0.05)
        budget.record("step1", "a", 0.03)
        assert budget.check_allowance(0.01) is True
        assert budget.check_allowance(0.03) is False

    def test_reset(self):
        budget = ErrorBudget(total_budget=0.05)
        budget.record("step1", "a", 0.03)
        budget.reset()
        assert budget.consumed == 0.0
