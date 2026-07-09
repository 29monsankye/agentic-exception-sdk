"""Tests for escalation: router, checkpoint store, and handlers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_exception_sdk.escalation.checkpoint import InMemoryCheckpointStore
from agentic_exception_sdk.escalation.handlers import NoOpHandler
from agentic_exception_sdk.escalation.router import (
    EscalationRouter,
    NoOpEscalationRouter,
    RecoveryDirective,
)
from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope, SafeContextSnapshot


def make_env(
    exception_class: AgentExceptionClass = AgentExceptionClass.EXCEPTION,
    suggested_recovery: EscalationLevel = EscalationLevel.L0_SELF_RETRY,
) -> AgentExceptionEnvelope:
    return AgentExceptionEnvelope(
        agent_id="test-agent",
        exception_class=exception_class,
        source=ExceptionSource.TOOL,
        error_type="Error",
        message="test",
        context_snapshot=SafeContextSnapshot({}),
        suggested_recovery=suggested_recovery,
        occurred_at=datetime.now(UTC),
    )


class TestRecoveryDirective:
    def test_resume_directive(self):
        d = RecoveryDirective(action="resume", resume_state={"x": 1})
        assert d.action == "resume"
        assert d.resume_state == {"x": 1}

    def test_escalate_directive(self):
        d = RecoveryDirective(action="escalate")
        assert d.action == "escalate"

    def test_abort_directive(self):
        d = RecoveryDirective(action="abort")
        assert d.action == "abort"

    def test_frozen(self):
        d = RecoveryDirective(action="resume")
        with pytest.raises(Exception):
            d.action = "abort"  # type: ignore[misc]


class TestNoOpHandler:
    def test_returns_none(self):
        env = make_env()
        assert NoOpHandler().handle(env) is None


class TestNoOpEscalationRouter:
    def test_returns_none(self):
        router = NoOpEscalationRouter()
        assert router.route(make_env()) is None


class TestEscalationRouter:
    def test_routes_exception_to_exception_handler(self):
        class RetryHandler:
            def handle(self, envelope):
                return RecoveryDirective(action="resume", resume_state="retried")

        router = EscalationRouter(
            exception_handlers={EscalationLevel.L0_SELF_RETRY: RetryHandler()}
        )
        env = make_env(AgentExceptionClass.EXCEPTION, EscalationLevel.L0_SELF_RETRY)
        directive = router.route(env)
        assert directive is not None
        assert directive.action == "resume"

    def test_routes_issue_to_issue_handler(self):
        class CheckpointHandler:
            def handle(self, envelope):
                return RecoveryDirective(action="resume", resume_state="checkpoint")

        router = EscalationRouter(
            issue_handlers={EscalationLevel.L2_CHECKPOINT_HANDOFF: CheckpointHandler()}
        )
        env = make_env(AgentExceptionClass.ISSUE, EscalationLevel.L2_CHECKPOINT_HANDOFF)
        directive = router.route(env)
        assert directive is not None
        assert directive.resume_state == "checkpoint"

    def test_routes_hard_kill_to_hard_kill_handler(self):
        class AbortHandler:
            def handle(self, envelope):
                return RecoveryDirective(action="abort")

        router = EscalationRouter(hard_kill_handler=AbortHandler())
        env = make_env(AgentExceptionClass.HARD_KILL, EscalationLevel.L4_SAFE_ABORT)
        directive = router.route(env)
        assert directive is not None
        assert directive.action == "abort"

    def test_returns_none_when_no_handler_matches(self):
        router = EscalationRouter()
        env = make_env(AgentExceptionClass.EXCEPTION, EscalationLevel.L0_SELF_RETRY)
        assert router.route(env) is None

    def test_exception_handler_not_used_for_issue(self):
        class BadHandler:
            def handle(self, envelope):
                raise AssertionError("should not be called")

        router = EscalationRouter(
            exception_handlers={EscalationLevel.L0_SELF_RETRY: BadHandler()}
        )
        env = make_env(AgentExceptionClass.ISSUE, EscalationLevel.L2_CHECKPOINT_HANDOFF)
        assert router.route(env) is None


class TestInMemoryCheckpointStore:
    def test_save_and_restore(self):
        store = InMemoryCheckpointStore()
        store.save(agent_id="a1", correlation_id="c1", state={"step": 5})
        result = store.restore(agent_id="a1", correlation_id="c1")
        assert result == {"step": 5}

    def test_restore_nonexistent_returns_none(self):
        store = InMemoryCheckpointStore()
        result = store.restore(agent_id="nonexistent", correlation_id=None)
        assert result is None

    def test_save_overwrites_previous(self):
        store = InMemoryCheckpointStore()
        store.save(agent_id="a1", correlation_id="c1", state={"step": 1})
        store.save(agent_id="a1", correlation_id="c1", state={"step": 2})
        assert store.restore(agent_id="a1", correlation_id="c1") == {"step": 2}

    def test_clear_removes_all(self):
        store = InMemoryCheckpointStore()
        store.save(agent_id="a1", correlation_id="c1", state="data")
        store.clear()
        assert store.restore(agent_id="a1", correlation_id="c1") is None

    def test_none_correlation_id_is_valid_key(self):
        store = InMemoryCheckpointStore()
        store.save(agent_id="a1", correlation_id=None, state="no-corr")
        assert store.restore(agent_id="a1", correlation_id=None) == "no-corr"
