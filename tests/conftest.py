"""Shared fixtures for unit and integration tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_exception_sdk import (
    AgentExceptionClass,
    AgentExceptionEnvelope,
    EscalationLevel,
    ExceptionSource,
    NoOpResilienceBundle,
    ResilienceBundle,
    SafeContextSnapshot,
)
from agentic_exception_sdk.propagation.bus import InMemoryBus


def make_envelope(
    *,
    exception_class: AgentExceptionClass = AgentExceptionClass.EXCEPTION,
    suggested_recovery: EscalationLevel = EscalationLevel.L0_SELF_RETRY,
    agent_id: str = "test-agent",
    correlation_id: str | None = "test-corr-01",
    message: str = "test error",
) -> AgentExceptionEnvelope:
    """Build a minimal valid envelope for testing."""
    return AgentExceptionEnvelope(
        agent_id=agent_id,
        exception_class=exception_class,
        source=ExceptionSource.TOOL,
        error_type="TestError",
        message=message,
        context_snapshot=SafeContextSnapshot({}),
        suggested_recovery=suggested_recovery,
        occurred_at=datetime.now(UTC),
        correlation_id=correlation_id,
    )


@pytest.fixture
def exception_envelope() -> AgentExceptionEnvelope:
    return make_envelope()


@pytest.fixture
def hard_kill_envelope() -> AgentExceptionEnvelope:
    return make_envelope(
        exception_class=AgentExceptionClass.HARD_KILL,
        suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
    )


@pytest.fixture
def issue_envelope() -> AgentExceptionEnvelope:
    return make_envelope(
        exception_class=AgentExceptionClass.ISSUE,
        suggested_recovery=EscalationLevel.L2_CHECKPOINT_HANDOFF,
    )


@pytest.fixture
def bundle() -> ResilienceBundle:
    return NoOpResilienceBundle()


@pytest.fixture
def bus() -> InMemoryBus:
    return InMemoryBus(max_size=100)
