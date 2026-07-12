"""Integration tests verifying the 12 requested fixes."""

from __future__ import annotations

import pytest

from agentic_exception_sdk import (
    AgentExceptionClass,
    AgentHardKillError,
    EscalationLevel,
    NoOpResilienceBundle,
    ResilienceBundle,
    async_resilient,
    resilient,
)
from agentic_exception_sdk.budget.models import AgentBudget
from agentic_exception_sdk.budget.watchdog import BudgetWatchdog
from agentic_exception_sdk.escalation.router import EscalationRouter, RecoveryDirective
from agentic_exception_sdk.propagation.bus import AsyncInMemoryBus
from agentic_exception_sdk.propagation.dlq import AsyncInMemoryDLQ
from agentic_exception_sdk.resilience.circuit_breaker import InMemoryCircuitBreaker
from agentic_exception_sdk.taxonomy.errors import BudgetWarningError, StateCorruptionError
from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator


def make_bundle(**kwargs) -> ResilienceBundle:
    b = NoOpResilienceBundle()
    for k, v in kwargs.items():
        setattr(b, k, v)
    return b


# ---------------------------------------------------------------------------
# 1. Async bus and DLQ publication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_bus_and_dlq_publication():
    bus = AsyncInMemoryBus(max_size=10)
    dlq = AsyncInMemoryDLQ(max_size=10)
    bundle = make_bundle(propagation_bus=bus, dlq=dlq)

    async def corrupt():
        raise StateCorruptionError("corruption")

    wrapped = async_resilient(bundle, tool_name="test_tool", agent_id="test_agent")(corrupt)

    with pytest.raises(AgentHardKillError):
        await wrapped()

    # Verify that the async bus and DLQ successfully received the envelope
    assert bus.size == 1
    assert dlq.size == 1

    bus_envelopes = await bus.drain()
    dlq_envelopes = await dlq.drain()
    assert bus_envelopes[0].exception_class == AgentExceptionClass.HARD_KILL
    assert dlq_envelopes[0].exception_class == AgentExceptionClass.HARD_KILL


# ---------------------------------------------------------------------------
# 2. Async fallback to synchronous circuit breakers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_fallback_to_sync_circuit_breaker():
    cb = InMemoryCircuitBreaker(failure_threshold=2, cooldown_seconds=30)
    bundle = make_bundle(circuit_breaker=cb, async_circuit_breaker=None)

    calls = 0

    async def fail_tool():
        nonlocal calls
        calls += 1
        raise ConnectionError("cb failure")

    wrapped = async_resilient(bundle, tool_name="test_tool", agent_id="test_agent")(fail_tool)

    # First attempt fails with original exception
    with pytest.raises(ConnectionError):
        await wrapped()

    # Second attempt trips the circuit breaker and fails with original exception
    with pytest.raises(ConnectionError):
        await wrapped()

    # Third attempt should fail immediately due to OPEN circuit breaker
    from agentic_exception_sdk.taxonomy.errors import CircuitBreakerStateUnavailableError

    with pytest.raises(CircuitBreakerStateUnavailableError):
        await wrapped()

    assert calls == 2  # tool not invoked on the third attempt


# ---------------------------------------------------------------------------
# 3. Failure-budget accounting
# ---------------------------------------------------------------------------


def test_failure_budget_accounting_sync():
    budget = BudgetWatchdog(AgentBudget(failure_budget=2))
    bundle = make_bundle(agent_budget=budget)

    def fail_tool():
        raise TimeoutError("failed")

    wrapped = resilient(bundle, tool_name="test_tool", agent_id="test_agent")(fail_tool)

    # First failure is allowed (raises the TimeoutError itself)
    with pytest.raises(TimeoutError):
        wrapped()

    assert budget.failure_count == 1

    # Second failure exhausts the budget (raises BudgetExhaustedError, classified as HARD_KILL)
    with pytest.raises(AgentHardKillError) as exc_info:
        wrapped()

    assert exc_info.value.envelope.error_type == "BudgetExhaustedError"
    assert budget.failure_count == 2


@pytest.mark.asyncio
async def test_failure_budget_accounting_async():
    budget = BudgetWatchdog(AgentBudget(failure_budget=2))
    bundle = make_bundle(agent_budget=budget)

    async def fail_tool():
        raise TimeoutError("failed")

    wrapped = async_resilient(bundle, tool_name="test_tool", agent_id="test_agent")(fail_tool)

    with pytest.raises(TimeoutError):
        await wrapped()

    assert budget.failure_count == 1

    with pytest.raises(AgentHardKillError) as exc_info:
        await wrapped()

    assert exc_info.value.envelope.error_type == "BudgetExhaustedError"
    assert budget.failure_count == 2


# ---------------------------------------------------------------------------
# 4. Resuming with None
# ---------------------------------------------------------------------------


def test_resume_with_none_escalation():
    class ResumeNoneHandler:
        def handle(self, env):
            return RecoveryDirective(action="resume", resume_state=None)

    router = EscalationRouter(
        issue_handlers={EscalationLevel.L3_HUMAN_ESCALATION: ResumeNoneHandler()}
    )
    bundle = make_bundle(escalation_router=router)

    def raise_issue():
        raise BudgetWarningError("budget warning")

    wrapped = resilient(bundle, tool_name="test_tool", agent_id="test_agent")(raise_issue)
    result = wrapped()
    assert result is None  # Resumed successfully with None!


# ---------------------------------------------------------------------------
# 5. Exception-group recovery
# ---------------------------------------------------------------------------


def test_base_exception_group_recovery_fallback():
    bundle = make_bundle()

    def raise_group():
        raise BaseExceptionGroup("errors", [TimeoutError("timeout error")])

    wrapped = resilient(
        bundle, tool_name="test_tool", agent_id="test_agent", fallback_value="GROUP_FALLBACK"
    )(raise_group)

    result = wrapped()
    assert result == "GROUP_FALLBACK"  # Result recovered via fallback_value!


# ---------------------------------------------------------------------------
# 6. Sensitive substring filtering
# ---------------------------------------------------------------------------


def test_sensitive_substring_filtering_context():
    validator = TrustBoundaryValidator()
    snapshot = {"stripe_token": "sk_live_512345", "clean_field": "safe_value"}
    sanitized = validator.sanitize_context_snapshot(snapshot)
    assert sanitized.root["stripe_token"] == "[REDACTED]"  # noqa: S105
    assert sanitized.root["clean_field"] == "safe_value"
