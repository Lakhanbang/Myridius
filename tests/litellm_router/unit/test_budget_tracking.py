"""Unit tests for :class:`litellm_router.protections.budget_tracking.BudgetTracker`."""

import pytest

from litellm_router.protections.budget_tracking import BudgetExceededError, BudgetTracker


def test_check_budget_passes_when_under_limit(fake_redis) -> None:
    tracker = BudgetTracker()
    tracker.record_cost(1.0)
    assert tracker.check_budget() == pytest.approx(1.0)


def test_check_budget_raises_when_limit_reached(
    fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_MONTHLY_BUDGET_USD", "2.00")
    tracker = BudgetTracker()
    tracker.record_cost(2.5)
    with pytest.raises(BudgetExceededError):
        tracker.check_budget()


def test_record_cost_ignores_non_positive(fake_redis) -> None:
    tracker = BudgetTracker()
    tracker.record_cost(0.0)
    tracker.record_cost(-5.0)
    assert tracker.get_current_spend() == 0.0
