"""Tests for budget watchdog."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agentic_exception_sdk.budget.models import AgentBudget, UnlimitedBudget
from agentic_exception_sdk.budget.watchdog import BudgetWatchdog
from agentic_exception_sdk.taxonomy.errors import BudgetExhaustedError, BudgetWarningError


def make_watchdog(**kwargs) -> BudgetWatchdog:
    return BudgetWatchdog(AgentBudget(**kwargs))


class TestAgentBudget:
    def test_unlimited_budget_has_no_ceilings(self):
        b = UnlimitedBudget()
        assert b.max_tool_calls is None
        assert b.failure_budget is None
        assert b.max_input_tokens is None

    def test_budget_immutable(self):
        b = AgentBudget(max_tool_calls=10)
        with pytest.raises(Exception):
            b.max_tool_calls = 20  # type: ignore[misc]


class TestBudgetWatchdog:
    def test_unlimited_consume_call_never_raises(self):
        wd = make_watchdog()
        for _ in range(1000):
            wd.consume_call()

    def test_max_tool_calls_enforced(self):
        wd = make_watchdog(max_tool_calls=2)
        wd.consume_call()
        wd.consume_call()
        with pytest.raises(BudgetExhaustedError):
            wd.consume_call()

    def test_failure_budget_enforced(self):
        wd = make_watchdog(failure_budget=2)
        wd.record_failure()  # 1 failure — below ceiling
        with pytest.raises(BudgetExhaustedError):
            wd.record_failure()  # 2 failures >= 2 ceiling

    def test_check_before_sleep_raises_when_exhausted(self):
        wd = make_watchdog(max_seconds=0.0)
        time.sleep(0.01)  # Ensure time has passed
        with pytest.raises((BudgetExhaustedError, BudgetWarningError)):
            wd.check_before_sleep(5.0)

    def test_reserve_tokens_atomic(self):
        wd = make_watchdog(max_input_tokens=100)
        wd.reserve_tokens(input_tokens=60)
        with pytest.raises(BudgetExhaustedError):
            wd.reserve_tokens(input_tokens=60)

    def test_reserve_tokens_concurrent_no_overspend(self):
        wd = make_watchdog(max_input_tokens=100)
        results = []
        errors = []

        def worker():
            try:
                wd.reserve_tokens(input_tokens=60)
                results.append("success")
            except BudgetExhaustedError:
                errors.append("exhausted")

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) <= 1, "Only one 60-token reservation should succeed with 100 ceiling"

    @pytest.mark.asyncio
    async def test_async_reserve_tokens_atomic(self):
        wd = make_watchdog(max_input_tokens=100)
        await wd.async_reserve_tokens(input_tokens=60)
        with pytest.raises(BudgetExhaustedError):
            await wd.async_reserve_tokens(input_tokens=60)

    @pytest.mark.asyncio
    async def test_async_concurrent_no_overspend(self):
        wd = make_watchdog(max_input_tokens=50)
        successes = []
        failures = []

        async def worker():
            try:
                await wd.async_reserve_tokens(input_tokens=40)
                successes.append(1)
            except BudgetExhaustedError:
                failures.append(1)

        await asyncio.gather(*[worker() for _ in range(5)])
        assert sum(successes) <= 1

    def test_account_stream_delta_raises_on_ceiling_exceeded(self):
        wd = make_watchdog(max_output_tokens=10)
        wd.reserve_tokens(output_tokens=5)
        with pytest.raises(BudgetExhaustedError):
            wd.account_stream_delta(output_tokens=10)

    def test_cost_ceiling_enforced(self):
        wd = make_watchdog(max_total_cost_micros_usd=1_000_000)
        wd.reserve_tokens(cost_micros_usd=600_000)
        with pytest.raises(BudgetExhaustedError):
            wd.reserve_tokens(cost_micros_usd=600_000)

    def test_snapshot_returns_counters_and_ceilings(self):
        wd = make_watchdog(
            max_seconds=60.0,
            max_tool_calls=3,
            failure_budget=2,
            max_input_tokens=100,
            max_output_tokens=200,
            max_total_cost_micros_usd=1_000_000,
        )
        wd.consume_call()
        wd.record_failure()
        wd.reserve_tokens(
            input_tokens=10,
            output_tokens=20,
            cost_micros_usd=30_000,
        )

        snapshot = wd.snapshot()

        assert set(snapshot) == {
            "call_count",
            "failure_count",
            "input_tokens_used",
            "output_tokens_used",
            "cost_micros_used",
            "elapsed_seconds",
            "max_tool_calls",
            "failure_budget",
            "max_input_tokens",
            "max_output_tokens",
            "max_total_cost_micros_usd",
            "max_seconds",
        }
        assert snapshot["call_count"] == 1
        assert snapshot["failure_count"] == 1
        assert snapshot["input_tokens_used"] == 10
        assert snapshot["output_tokens_used"] == 20
        assert snapshot["cost_micros_used"] == 30_000
        assert snapshot["elapsed_seconds"] >= 0
        assert snapshot["max_tool_calls"] == 3
        assert snapshot["failure_budget"] == 2
        assert snapshot["max_input_tokens"] == 100
        assert snapshot["max_output_tokens"] == 200
        assert snapshot["max_total_cost_micros_usd"] == 1_000_000
        assert snapshot["max_seconds"] == 60.0

    def test_snapshot_reports_none_for_unset_ceilings_and_does_not_mutate(self):
        wd = make_watchdog()

        first = wd.snapshot()
        first["call_count"] = 99
        second = wd.snapshot()

        assert second["call_count"] == 0
        assert second["max_tool_calls"] is None
        assert second["failure_budget"] is None
        assert second["max_input_tokens"] is None
        assert second["max_output_tokens"] is None
        assert second["max_total_cost_micros_usd"] is None
        assert second["max_seconds"] is None
