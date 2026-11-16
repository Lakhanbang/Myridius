"""Unit tests for RFP orchestrator per-agent metric accounting.

The orchestrator's ``_snapshot_metrics`` / ``_calculate_metrics`` pair must
report each agent's own time/cost/tokens while still producing a session
total that equals the sum of all agents. These tests exercise that logic
with a fake gateway/budget tracker, so no Redis or network is needed.
"""

import time

import pytest

from rfp.rfp_orchestrator import RFPOrchestrator


class FakeBudgetTracker:
    """In-memory stand-in for :class:`BudgetTracker`."""

    def __init__(self) -> None:
        self.spend = 0.0
        self.max_monthly_budget_usd = 50.0

    def get_current_spend(self) -> float:
        return self.spend

    def record_cost(self, cost: float) -> None:
        self.spend += cost


class FakeGateway:
    """In-memory stand-in for the LLM gateway (tokens + budget tracker)."""

    def __init__(self) -> None:
        self.session_tokens = 0
        self.budget_tracker = FakeBudgetTracker()


def make_orchestrator() -> RFPOrchestrator:
    """Build an RFPOrchestrator without triggering __init__ (no MCP/CrewAI)."""
    orch = object.__new__(RFPOrchestrator)
    orch.gateway = FakeGateway()
    return orch


def test_metrics_isolated_per_agent_and_session_total() -> None:
    orch = make_orchestrator()
    orch.gateway.session_tokens = 0
    budget_before, tokens_before, start_time = orch._snapshot_metrics()

    # Organizer: 2000 tokens, $0.02, ~10ms
    b, tb, ts = orch._snapshot_metrics()
    orch.gateway.session_tokens += 2000
    orch.gateway.budget_tracker.record_cost(0.02)
    time.sleep(0.01)
    _, cost_org, _, tokens_org = orch._calculate_metrics(b, tb, ts)
    assert tokens_org == 2000
    assert cost_org == pytest.approx(0.02)

    # Retriever: 1500 tokens, $0.005, ~10ms
    b, tb, ts = orch._snapshot_metrics()
    orch.gateway.session_tokens += 1500
    orch.gateway.budget_tracker.record_cost(0.005)
    time.sleep(0.01)
    _, cost_ret, _, tokens_ret = orch._calculate_metrics(b, tb, ts)
    assert tokens_ret == 1500
    assert cost_ret == pytest.approx(0.005)

    # Drafter: 3000 tokens, $0.03, ~10ms
    b, tb, ts = orch._snapshot_metrics()
    orch.gateway.session_tokens += 3000
    orch.gateway.budget_tracker.record_cost(0.03)
    time.sleep(0.01)
    time_d, cost_d, _, tokens_d = orch._calculate_metrics(b, tb, ts)
    assert tokens_d == 3000
    assert cost_d == pytest.approx(0.03)

    # Session total must equal the sum of all agents (2000 + 1500 + 3000).
    time_total, cost_total, remaining_budget, tokens_total = orch._calculate_metrics(
        budget_before, tokens_before, start_time
    )
    assert tokens_total == 6500
    assert cost_total == pytest.approx(0.055)
    assert remaining_budget == pytest.approx(50.0 - 0.055)
    assert time_total >= time_d


def test_drafter_metrics_accumulate_across_retry_attempts() -> None:
    orch = make_orchestrator()
    orch.gateway.session_tokens = 0

    # Snapshot once before the retry loop (matches the orchestrator change).
    b, tb, ts = orch._snapshot_metrics()
    orch.gateway.session_tokens += 2000  # failed attempt
    orch.gateway.budget_tracker.record_cost(0.01)
    orch.gateway.session_tokens += 1000  # successful attempt
    orch.gateway.budget_tracker.record_cost(0.005)

    _, cost_d, _, tokens_d = orch._calculate_metrics(b, tb, ts)
    assert tokens_d == 3000
    assert cost_d == pytest.approx(0.015)


def test_session_total_with_retries_and_retriever_tokens() -> None:
    orch = make_orchestrator()
    orch.gateway.session_tokens = 0
    budget_before, tokens_before, start_time = orch._snapshot_metrics()

    # Organizer
    b, tb, ts = orch._snapshot_metrics()
    orch.gateway.session_tokens += 1000
    orch.gateway.budget_tracker.record_cost(0.01)
    _, _, _, tokens_org = orch._calculate_metrics(b, tb, ts)

    # Retriever (embedding tokens added directly, like search_company_knowledge_raw)
    b, tb, ts = orch._snapshot_metrics()
    orch.gateway.session_tokens += 800
    orch.gateway.budget_tracker.record_cost(0.002)
    _, _, _, tokens_ret = orch._calculate_metrics(b, tb, ts)

    # Drafter with two attempts
    b, tb, ts = orch._snapshot_metrics()
    orch.gateway.session_tokens += 1500
    orch.gateway.budget_tracker.record_cost(0.02)
    orch.gateway.session_tokens += 1200
    orch.gateway.budget_tracker.record_cost(0.015)
    _, _, _, tokens_drafter = orch._calculate_metrics(b, tb, ts)

    _, _, _, tokens_total = orch._calculate_metrics(budget_before, tokens_before, start_time)

    assert tokens_org == 1000
    assert tokens_ret == 800
    assert tokens_drafter == 2700
    assert tokens_total == 1000 + 800 + 2700
