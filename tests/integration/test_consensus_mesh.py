"""Integration tests: multi-agent consensus and orchestrator mesh."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agentic_exception_sdk import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
    NoOpResilienceBundle,
    SafeContextSnapshot,
    async_resilient,
)
from agentic_exception_sdk.escalation.router import RecoveryDirective
from agentic_exception_sdk.multi_agent.consensus import ConsensusGate, ConsensusNotReachedError
from agentic_exception_sdk.multi_agent.router import OrchestratorExceptionRouter, fan_out
from agentic_exception_sdk.multi_agent.sla import AgentSLAPolicy
from agentic_exception_sdk.propagation.bus import InMemoryBus
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope


def make_env(**kwargs) -> AgentExceptionEnvelope:
    defaults = {
        "agent_id": "orch-agent",
        "exception_class": AgentExceptionClass.EXCEPTION,
        "source": ExceptionSource.ORCHESTRATION,
        "error_type": "Error",
        "message": "test",
        "context_snapshot": SafeContextSnapshot({}),
        "suggested_recovery": EscalationLevel.L0_SELF_RETRY,
        "occurred_at": datetime.now(UTC),
    }
    defaults.update(kwargs)
    return AgentExceptionEnvelope(**defaults)


# ---------------------------------------------------------------------------
# Scenario 1: K-of-N consensus gate across a fan-out
# ---------------------------------------------------------------------------

class TestConsensusGateIntegration:
    @pytest.mark.asyncio
    async def test_k_of_n_consensus_reached(self):
        gate = ConsensusGate(threshold=2, correlation_id="mesh-trace-01")
        votes_cast = []

        async def worker_handler(env):
            gate.vote(env)
            votes_cast.append(True)
            return RecoveryDirective(action="resume", resume_state="voted")

        async def observer_handler(env):
            return None

        env = make_env()
        await fan_out(env, [worker_handler, worker_handler, observer_handler])

        gate.require_consensus()  # Should not raise — 2 votes, threshold=2
        assert len(votes_cast) == 2

    @pytest.mark.asyncio
    async def test_consensus_not_reached_raises(self):
        gate = ConsensusGate(threshold=3, correlation_id="mesh-trace-02")

        async def partial_voter(env):
            gate.vote(env)
            return None

        env = make_env()
        await fan_out(env, [partial_voter, partial_voter])

        with pytest.raises(ConsensusNotReachedError):
            gate.require_consensus()

    @pytest.mark.asyncio
    async def test_failed_handlers_do_not_count(self):
        gate = ConsensusGate(threshold=2)

        async def good_voter(env):
            gate.vote(env)
            return RecoveryDirective(action="resume")

        async def bad_voter(env):
            raise RuntimeError("handler crashed before voting")

        env = make_env()
        await fan_out(env, [good_voter, bad_voter])

        assert gate.vote_count == 1
        with pytest.raises(ConsensusNotReachedError):
            gate.require_consensus()


# ---------------------------------------------------------------------------
# Scenario 2: Orchestrator routes ISSUE to worker agents
# ---------------------------------------------------------------------------

class TestOrchestratorMesh:
    @pytest.mark.asyncio
    async def test_orchestrator_routes_to_first_responding_worker(self):
        responses = []

        async def worker_a(env):
            responses.append("a")
            return RecoveryDirective(action="resume", resume_state={"worker": "a"})

        async def worker_b(env):
            responses.append("b")
            return RecoveryDirective(action="resume", resume_state={"worker": "b"})

        router = OrchestratorExceptionRouter([worker_a, worker_b])
        env = make_env()
        directive = await router.route(env)

        assert directive is not None
        assert directive.resume_state["worker"] in ("a", "b")

    @pytest.mark.asyncio
    async def test_all_failing_workers_returns_none(self):
        async def fail(env):
            raise RuntimeError("worker crashed")

        router = OrchestratorExceptionRouter([fail, fail])
        env = make_env()
        directive = await router.route(env)
        assert directive is None

    @pytest.mark.asyncio
    async def test_timeout_on_slow_workers(self):
        async def slow_worker(env):
            await asyncio.sleep(10.0)
            return RecoveryDirective(action="resume")

        router = OrchestratorExceptionRouter(
            [slow_worker], fan_out_timeout_seconds=0.01
        )
        env = make_env()
        with pytest.raises((asyncio.TimeoutError, BaseExceptionGroup)):
            await router.route(env)


# ---------------------------------------------------------------------------
# Scenario 3: SLA-aware multi-agent run
# ---------------------------------------------------------------------------

class TestSLAAwareRun:
    @pytest.mark.asyncio
    async def test_sla_check_within_deadline_allows_run(self):
        policy = AgentSLAPolicy(max_seconds=60.0, agent_id="sla-test-agent")
        b = NoOpResilienceBundle()

        async def agent_step():
            policy.check()
            return "ok"

        wrapped = async_resilient(b, tool_name="sla-tool", agent_id="sla-test-agent")(
            agent_step
        )
        result = await wrapped()
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_sla_violation_propagates_as_exception(self):
        policy = AgentSLAPolicy(max_seconds=0.0, agent_id="sla-test-agent")
        await asyncio.sleep(0.01)  # Ensure deadline exceeded

        b = NoOpResilienceBundle()
        bus = InMemoryBus()
        b.propagation_bus = bus

        async def agent_step():
            policy.check()  # Raises SLAViolationError
            return "unreachable"

        wrapped = async_resilient(
            b, tool_name="sla-tool", agent_id="sla-test-agent"
        )(agent_step)

        with pytest.raises(Exception):  # SLAViolation → ISSUE → re-raised
            await wrapped()

        # Envelope should have been published
        envelopes = bus.drain()
        assert len(envelopes) >= 1


# ---------------------------------------------------------------------------
# Scenario 4: Multi-agent lineage propagation
# ---------------------------------------------------------------------------

class TestLineagePropagation:
    def test_lineage_tracks_agent_hops(self):
        from agentic_exception_sdk.resilience.wrap import extend_lineage

        env = make_env(lineage=["agent-origin"])
        env2 = extend_lineage(env, "agent-worker-1")
        env3 = extend_lineage(env2, "agent-worker-2")

        assert env3.lineage == ["agent-origin", "agent-worker-1", "agent-worker-2"]

    def test_lineage_cap_produces_hard_kill_envelope(self):
        from agentic_exception_sdk.resilience.wrap import extend_lineage

        lineage = [f"agent-{i}" for i in range(64)]
        env = make_env(lineage=lineage)
        result = extend_lineage(env, "overflow-agent")
        assert result.exception_class == AgentExceptionClass.HARD_KILL
        assert result.error_type == "LineageCapExceededError"
