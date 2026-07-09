"""Tests for persistence provider boundary and NullProvider."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_exception_sdk.persistence import (
    NullProvider,
    PersistenceProvider,
    attestation,
    set_active_provider,
)
from agentic_exception_sdk.propagation.protocol import envelope_leaf_hash
from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope, SafeContextSnapshot


@pytest.fixture(autouse=True)
def reset_active_provider():
    set_active_provider(NullProvider())
    yield
    set_active_provider(NullProvider())


def make_env() -> AgentExceptionEnvelope:
    return AgentExceptionEnvelope(
        agent_id="persist-agent",
        exception_class=AgentExceptionClass.EXCEPTION,
        source=ExceptionSource.TOOL,
        error_type="TimeoutError",
        message="timeout",
        context_snapshot=SafeContextSnapshot({}),
        suggested_recovery=EscalationLevel.L0_SELF_RETRY,
        occurred_at=datetime.now(UTC),
    )


def test_null_provider_matches_persistence_protocol():
    provider = NullProvider()
    assert isinstance(provider, PersistenceProvider)


def test_null_provider_persist_computes_leaf_hash():
    provider = NullProvider()
    env = make_env()

    persisted = provider.persist(env)

    assert persisted.exception_id == env.exception_id
    assert persisted.leaf_hash == envelope_leaf_hash(env)


def test_null_provider_checkpoint_uses_current_merkle_root():
    provider = NullProvider()
    first = provider.persist(make_env()).leaf_hash

    checkpoint = provider.checkpoint(batch_id="batch-1")

    assert checkpoint.batch_id == "batch-1"
    assert checkpoint.root == first
    assert checkpoint.signed is False


def test_attestation_reports_active_null_provider_capabilities():
    provider = NullProvider()
    set_active_provider(provider)

    assert attestation() == {
        "durable": False,
        "checkpoint_signing": False,
        "worm": False,
    }
