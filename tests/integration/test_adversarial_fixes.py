"""Regression tests for the adversarial review of the Gemini fixes.

Each test pins behavior that was broken (or newly broken) and would have caught
the corresponding finding. See the findings write-up for the mapping.
"""

from __future__ import annotations

import pytest

from agentic_exception_sdk import (
    AgentHardKillError,
    NoOpResilienceBundle,
    ResilienceBundle,
    resilient,
)
from agentic_exception_sdk.budget.models import AgentBudget
from agentic_exception_sdk.budget.watchdog import BudgetWatchdog
from agentic_exception_sdk.multi_agent.consensus import ConsensusGate
from agentic_exception_sdk.multi_agent.parallel import call_parallel
from agentic_exception_sdk.propagation.bus import AsyncInMemoryBus
from agentic_exception_sdk.propagation.dlq import AsyncInMemoryDLQ
from agentic_exception_sdk.resilience import wrap as wrap_module
from agentic_exception_sdk.resilience.circuit_breaker import CircuitState, InMemoryCircuitBreaker
from agentic_exception_sdk.resilience.retry import (
    ExponentialBackoffRetry,
    InMemoryRetryInFlightTracker,
    RetryContext,
)
from agentic_exception_sdk.taxonomy.errors import StateCorruptionError
from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator


def make_bundle(**kwargs) -> ResilienceBundle:
    b = NoOpResilienceBundle()
    for k, v in kwargs.items():
        setattr(b, k, v)
    return b


# ---------------------------------------------------------------------------
# Finding 4 (#10): sensitive-key redaction must not over-redact ordinary words
# ---------------------------------------------------------------------------


def test_sensitive_key_redaction_no_false_positives():
    tb = TrustBoundaryValidator()
    snap = tb.sanitize_context_snapshot(
        {
            # Sensitive — must be redacted.
            "password": "hunter2",
            "stripe_token": "sk_live_1",
            "api_key": "abc",
            "accessToken": "xyz",
            "key": "primary",
            # Ordinary words that merely contain a sensitive substring — must NOT.
            "author": "Jane Doe",
            "monkey_count": 3,
            "keyword": "sale",
            "turkey_region": "eu",
            "whiskey_brand": "x",
            "keyboard_layout": "qwerty",
        }
    )
    root = snap.root
    assert root["password"] == "[REDACTED]"  # noqa: S105
    assert root["stripe_token"] == "[REDACTED]"  # noqa: S105
    assert root["api_key"] == "[REDACTED]"
    assert root["accessToken"] == "[REDACTED]"
    assert root["key"] == "[REDACTED]"
    assert root["author"] == "Jane Doe"
    assert root["monkey_count"] == 3
    assert root["keyword"] == "sale"
    assert root["turkey_region"] == "eu"
    assert root["whiskey_brand"] == "x"
    assert root["keyboard_layout"] == "qwerty"


# ---------------------------------------------------------------------------
# Finding 5 (#5): malformed SDK_SYNC_TIMEOUT_WORKERS must not crash at import
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["abc", "0", "-4", "", "3.5"])
def test_sync_timeout_workers_falls_back_on_bad_env(monkeypatch, bad):
    monkeypatch.setenv("SDK_SYNC_TIMEOUT_WORKERS", bad)
    assert wrap_module._sync_timeout_workers() == 128


def test_sync_timeout_workers_honours_valid_env(monkeypatch):
    monkeypatch.setenv("SDK_SYNC_TIMEOUT_WORKERS", "8")
    assert wrap_module._sync_timeout_workers() == 8


# ---------------------------------------------------------------------------
# Finding 1 (#1): sync pipeline must not drop envelopes into async bus/DLQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_pipeline_publishes_to_async_bus_and_dlq():
    bus = AsyncInMemoryBus(max_size=10)
    dlq = AsyncInMemoryDLQ(max_size=10)
    bundle = make_bundle(propagation_bus=bus, dlq=dlq)

    def corrupt():
        raise StateCorruptionError("corruption")

    # Synchronous resilient() with async-native bus/DLQ (shared-bundle scenario).
    wrapped = resilient(bundle, tool_name="tool", agent_id="agent")(corrupt)
    with pytest.raises(AgentHardKillError):
        wrapped()

    assert bus.size == 1
    assert dlq.size == 1
    assert (await dlq.drain())[0].exception_class.value == "hard_kill"


# ---------------------------------------------------------------------------
# Finding 2 (#7): call_parallel sync timeouts must be classified & recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_parallel_sync_timeout_produces_telemetry():
    import time

    budget = BudgetWatchdog(AgentBudget())
    bundle = make_bundle(agent_budget=budget)

    def slow():
        time.sleep(0.3)
        return "never"

    _results, envelope = await call_parallel(
        [(bundle, "slow_tool", "agent", slow)],
        correlation_id="corr",
        timeout_seconds=0.05,
    )

    outcome = envelope.outcomes[0]
    assert outcome.status == "exception"
    # Before the fix the timeout was swallowed by an outer asyncio.timeout():
    # no envelope, no budget failure. Now it is classified and recorded.
    assert outcome.envelope is not None
    assert budget.failure_count >= 1


# ---------------------------------------------------------------------------
# Finding 3 (#12): ConsensusGate must expose cleanup + per-correlation reads
# ---------------------------------------------------------------------------


def test_consensus_gate_reset_and_per_correlation_reads():
    from dataclasses import dataclass

    @dataclass
    class _Env:  # vote() only reads .correlation_id
        correlation_id: str

    gate = ConsensusGate(threshold=2)

    def env(cid: str) -> _Env:
        return _Env(correlation_id=cid)

    gate.vote(env("run-a"))
    gate.vote(env("run-a"))
    gate.vote(env("run-b"))

    assert gate.votes_for("run-a") == 2
    assert gate.votes_for("run-b") == 1
    assert gate.reached("run-a") is True
    assert gate.reached("run-b") is False

    # reset() releases one correlation window; clear() releases all.
    gate.reset("run-a")
    assert gate.votes_for("run-a") == 0
    assert gate.votes_for("run-b") == 1
    gate.clear()
    assert gate.votes_for("run-b") == 0


# ---------------------------------------------------------------------------
# Finding 6 (#3): budget exhaustion must not pollute the circuit breaker
# ---------------------------------------------------------------------------


def test_budget_exhaustion_does_not_trip_circuit_breaker():
    cb = InMemoryCircuitBreaker(failure_threshold=1, cooldown_seconds=30)
    budget = BudgetWatchdog(AgentBudget(failure_budget=1))
    bundle = make_bundle(circuit_breaker=cb, agent_budget=budget)

    def fail():
        raise ValueError("downstream")

    wrapped = resilient(bundle, tool_name="tool", agent_id="agent")(fail)
    # First failure immediately exhausts the (size-1) failure budget -> HARD_KILL.
    with pytest.raises(AgentHardKillError):
        wrapped()

    # The breaker (threshold=1) must remain CLOSED: budget exhaustion is an
    # internal signal, not a downstream fault.
    assert cb.state is CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Finding 6 (#3): retry in-flight tracker must be cleared on non-retryable exit
# ---------------------------------------------------------------------------


def test_retry_tracker_cleared_on_nonretryable_exit():
    tracker = InMemoryRetryInFlightTracker()
    policy = ExponentialBackoffRetry(
        max_attempts=3,
        base_delay_seconds=0.0,
        jitter=False,
        retryable_exceptions=(ValueError,),
        inflight_tracker=tracker,
    )

    calls = 0

    def fn():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("retryable")  # records an in-flight entry
        raise KeyError("non-retryable")  # exits loop without cleanup (old bug)

    with pytest.raises(KeyError):
        policy.execute(fn, context=RetryContext(correlation_id="corr-1"))

    assert tracker.list_active() == []
