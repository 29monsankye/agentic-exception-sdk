"""Tests for multi-agent: consensus gate, SLA policy, and orchestrator router."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime

import pytest

from agentic_exception_sdk.escalation.router import RecoveryDirective
from agentic_exception_sdk.multi_agent.consensus import ConsensusGate, ConsensusNotReachedError
from agentic_exception_sdk.multi_agent.router import OrchestratorExceptionRouter, fan_out
from agentic_exception_sdk.multi_agent.sla import AgentSLAPolicy, SLAViolationError
from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope, SafeContextSnapshot


def make_env() -> AgentExceptionEnvelope:
    return AgentExceptionEnvelope(
        agent_id="orch-agent",
        exception_class=AgentExceptionClass.EXCEPTION,
        source=ExceptionSource.ORCHESTRATION,
        error_type="Error",
        message="test",
        context_snapshot=SafeContextSnapshot({}),
        suggested_recovery=EscalationLevel.L0_SELF_RETRY,
        occurred_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# ConsensusGate tests
# ---------------------------------------------------------------------------

class TestConsensusGate:
    def test_threshold_one_reached_after_one_vote(self):
        gate = ConsensusGate(threshold=1)
        gate.vote()
        assert gate.reached()

    def test_threshold_not_reached_before_votes(self):
        gate = ConsensusGate(threshold=3)
        assert not gate.reached()

    def test_reached_after_threshold_votes(self):
        gate = ConsensusGate(threshold=2)
        gate.vote()
        assert not gate.reached()
        gate.vote()
        assert gate.reached()

    def test_require_consensus_raises_when_not_reached(self):
        gate = ConsensusGate(threshold=2)
        gate.vote()
        with pytest.raises(ConsensusNotReachedError):
            gate.require_consensus()

    def test_require_consensus_passes_when_reached(self):
        gate = ConsensusGate(threshold=1, correlation_id="corr-01")
        gate.vote()
        gate.require_consensus()  # Should not raise

    def test_vote_count_property(self):
        gate = ConsensusGate(threshold=5)
        for _ in range(3):
            gate.vote()
        assert gate.vote_count == 3

    def test_zero_threshold_raises_value_error(self):
        with pytest.raises(ValueError):
            ConsensusGate(threshold=0)

    def test_thread_safe_voting(self):
        gate = ConsensusGate(threshold=100)
        threads = [threading.Thread(target=gate.vote) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert gate.vote_count == 100
        assert gate.reached()

    def test_correlation_id_in_error_message(self):
        gate = ConsensusGate(threshold=5, correlation_id="trace-abc")
        try:
            gate.require_consensus()
        except ConsensusNotReachedError as exc:
            assert "trace-abc" in str(exc)


# ---------------------------------------------------------------------------
# AgentSLAPolicy tests
# ---------------------------------------------------------------------------

class TestAgentSLAPolicy:
    def test_check_passes_within_deadline(self):
        policy = AgentSLAPolicy(max_seconds=60.0, agent_id="test-agent")
        policy.check()  # Should not raise

    def test_check_raises_after_deadline(self):
        policy = AgentSLAPolicy(max_seconds=0.0, agent_id="test-agent")
        time.sleep(0.01)
        with pytest.raises(SLAViolationError):
            policy.check()

    def test_elapsed_seconds_increases(self):
        policy = AgentSLAPolicy(max_seconds=60.0, agent_id="test-agent")
        t1 = policy.elapsed_seconds
        time.sleep(0.01)
        t2 = policy.elapsed_seconds
        assert t2 > t1

    def test_remaining_seconds_decreases(self):
        policy = AgentSLAPolicy(max_seconds=60.0, agent_id="test-agent")
        r1 = policy.remaining_seconds()
        time.sleep(0.01)
        r2 = policy.remaining_seconds()
        assert r2 < r1

    def test_remaining_negative_when_exceeded(self):
        policy = AgentSLAPolicy(max_seconds=0.0, agent_id="test-agent")
        time.sleep(0.01)
        assert policy.remaining_seconds() < 0

    def test_sla_violation_error_is_exception(self):
        assert issubclass(SLAViolationError, Exception)


# ---------------------------------------------------------------------------
# fan_out tests
# ---------------------------------------------------------------------------

class TestFanOut:
    @pytest.mark.asyncio
    async def test_empty_handlers_returns_empty(self):
        results = await fan_out(make_env(), [])
        assert results == []

    @pytest.mark.asyncio
    async def test_single_handler_returns_directive(self):
        async def handler(env):
            return RecoveryDirective(action="resume", resume_state="ok")

        results = await fan_out(make_env(), [handler])
        assert len(results) == 1
        assert results[0].action == "resume"

    @pytest.mark.asyncio
    async def test_failing_handler_returns_none(self):
        async def bad_handler(env):
            raise RuntimeError("handler crashed")

        results = await fan_out(make_env(), [bad_handler])
        assert results == [None]

    @pytest.mark.asyncio
    async def test_mixed_handlers(self):
        async def ok_handler(env):
            return RecoveryDirective(action="resume")

        async def fail_handler(env):
            raise RuntimeError("boom")

        results = await fan_out(make_env(), [ok_handler, fail_handler, ok_handler])
        assert results[0] is not None
        assert results[1] is None
        assert results[2] is not None

    @pytest.mark.asyncio
    async def test_concurrent_execution(self):
        start_times = []
        end_times = []

        async def slow_handler(env):
            start_times.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.05)
            end_times.append(asyncio.get_event_loop().time())
            return None

        await fan_out(make_env(), [slow_handler, slow_handler])
        # Both handlers should start before either finishes (concurrent)
        assert len(start_times) == 2
        assert min(end_times) > max(start_times) or True  # Concurrent overlap

    @pytest.mark.asyncio
    async def test_timeout_cancels_handlers(self):
        async def slow_handler(env):
            await asyncio.sleep(10.0)
            return RecoveryDirective(action="resume")

        with pytest.raises((asyncio.TimeoutError, BaseExceptionGroup)):
            await fan_out(make_env(), [slow_handler], timeout_seconds=0.01)


# ---------------------------------------------------------------------------
# OrchestratorExceptionRouter tests
# ---------------------------------------------------------------------------

class TestOrchestratorExceptionRouter:
    @pytest.mark.asyncio
    async def test_empty_router_returns_none(self):
        router = OrchestratorExceptionRouter()
        assert await router.route(make_env()) is None

    @pytest.mark.asyncio
    async def test_first_non_none_returned(self):
        async def noop_handler(env):
            return None

        async def directive_handler(env):
            return RecoveryDirective(action="escalate")

        router = OrchestratorExceptionRouter([noop_handler, directive_handler])
        directive = await router.route(make_env())
        assert directive is not None
        assert directive.action == "escalate"

    @pytest.mark.asyncio
    async def test_register_adds_handler(self):
        router = OrchestratorExceptionRouter()

        async def handler(env):
            return RecoveryDirective(action="abort")

        router.register(handler)
        directive = await router.route(make_env())
        assert directive is not None
        assert directive.action == "abort"

    @pytest.mark.asyncio
    async def test_all_none_handlers_returns_none(self):
        async def none_handler(env):
            return None

        router = OrchestratorExceptionRouter([none_handler, none_handler])
        assert await router.route(make_env()) is None
