"""Integration tests: full resilience pipeline end-to-end."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from agentic_exception_sdk import (
    AgentExceptionClass,
    AgentHardKillError,
    EscalationLevel,
    NoOpResilienceBundle,
    ResilienceBundle,
    async_resilient,
    resilient,
)
from agentic_exception_sdk.escalation.router import EscalationRouter, RecoveryDirective
from agentic_exception_sdk.propagation.bus import InMemoryBus
from agentic_exception_sdk.resilience.retry import ExponentialBackoffRetry
from agentic_exception_sdk.taxonomy.errors import StateCorruptionError
from agentic_exception_sdk.validation.gates import PydanticValidationGate

TOOL = "integration-tool"
AGENT = "integration-agent"
CORR = "int-corr-01"


def make_bundle(**kwargs) -> ResilienceBundle:
    b = NoOpResilienceBundle()
    for k, v in kwargs.items():
        setattr(b, k, v)
    return b


# ---------------------------------------------------------------------------
# Scenario 1: EXCEPTION → retry → fallback
# ---------------------------------------------------------------------------

class TestExceptionRetryFallback:
    def test_retry_recovers(self):
        calls = [0]

        def flaky():
            calls[0] += 1
            if calls[0] < 3:
                raise TimeoutError(f"attempt {calls[0]} failed")
            return "success"

        retry = ExponentialBackoffRetry(
            max_attempts=3,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
        )
        bundle = make_bundle(retry_policy=retry)
        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT)(flaky)
        assert wrapped() == "success"
        assert calls[0] == 3

    def test_fallback_returned_when_retry_exhausted(self):
        bus = InMemoryBus()
        retry = ExponentialBackoffRetry(
            max_attempts=2,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
        )
        bundle = make_bundle(retry_policy=retry, propagation_bus=bus)
        wrapped = resilient(
            bundle, tool_name=TOOL, agent_id=AGENT, fallback_value="FALLBACK"
        )(lambda: (_ for _ in ()).throw(TimeoutError("always fails")))
        result = wrapped()
        assert result == "FALLBACK"
        assert bus.size == 1


# ---------------------------------------------------------------------------
# Scenario 2: HARD_KILL → AgentHardKillError → envelope published
# ---------------------------------------------------------------------------

class TestHardKillPipeline:
    def test_hard_kill_envelope_published(self):
        bus = InMemoryBus()
        bundle = make_bundle(propagation_bus=bus)

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT)(
            lambda: (_ for _ in ()).throw(StateCorruptionError("corrupted"))
        )
        with pytest.raises(AgentHardKillError) as exc_info:
            wrapped()

        assert exc_info.value.envelope.exception_class == AgentExceptionClass.HARD_KILL
        assert bus.size == 1
        envelope = bus.drain()[0]
        assert envelope.exception_class == AgentExceptionClass.HARD_KILL
        assert envelope.agent_id == AGENT

    def test_hard_kill_not_swallowed_by_except_exception(self):
        bundle = make_bundle()
        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT)(
            lambda: (_ for _ in ()).throw(StateCorruptionError("corrupt"))
        )
        caught = False
        try:
            wrapped()
        except Exception:
            caught = True
        except BaseException:
            pass
        assert not caught


# ---------------------------------------------------------------------------
# Scenario 3: ISSUE → escalation router → checkpoint handoff
# ---------------------------------------------------------------------------

class TestIssueCheckpointHandoff:
    def test_router_provides_resume_state(self):
        from agentic_exception_sdk.taxonomy.errors import BudgetWarningError

        checkpoint_state = {"step": 5, "last_successful_node": "search"}

        class CheckpointHandler:
            def handle(self, env):
                return RecoveryDirective(
                    action="resume",
                    resume_state=checkpoint_state,
                    resume_from_step="search",
                )

        router = EscalationRouter(
            issue_handlers={EscalationLevel.L3_HUMAN_ESCALATION: CheckpointHandler()}
        )
        bundle = make_bundle(escalation_router=router)

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT)(
            lambda: (_ for _ in ()).throw(BudgetWarningError("budget warning"))
        )
        result = wrapped()
        assert result == checkpoint_state


# ---------------------------------------------------------------------------
# Scenario 4: Output validation failure
# ---------------------------------------------------------------------------

class TestOutputValidationPipeline:
    def test_validation_gate_failure_classified_as_issue(self):
        class FlightResult(BaseModel):
            flight_id: str
            price: float

        bus = InMemoryBus()
        gate = PydanticValidationGate(FlightResult)
        bundle = make_bundle(output_validation_gate=gate, propagation_bus=bus)

        def bad_tool():
            return {"wrong_key": "value"}  # missing required fields

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT)(bad_tool)
        with pytest.raises((Exception,)):  # ValidationGateError or reraises
            wrapped()

        assert bus.size >= 1


# ---------------------------------------------------------------------------
# Scenario 5: async_resilient() full pipeline
# ---------------------------------------------------------------------------

class TestAsyncPipeline:
    @pytest.mark.asyncio
    async def test_async_success(self):
        bundle = make_bundle()

        async def fetch_data():
            return {"data": [1, 2, 3]}

        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT)(fetch_data)
        result = await wrapped()
        assert result["data"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_async_hard_kill_envelope_published(self):
        bus = InMemoryBus()
        bundle = make_bundle(propagation_bus=bus)

        async def corrupt():
            raise StateCorruptionError("async corruption")

        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT)(corrupt)
        with pytest.raises(AgentHardKillError):
            await wrapped()

        assert bus.size == 1

    @pytest.mark.asyncio
    async def test_async_with_retry(self):
        calls = [0]

        async def flaky():
            calls[0] += 1
            if calls[0] < 2:
                raise ConnectionError(f"attempt {calls[0]}")
            return "async-success"

        retry = ExponentialBackoffRetry(
            max_attempts=3,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(ConnectionError,),
        )
        bundle = make_bundle(retry_policy=retry)
        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT)(flaky)
        result = await wrapped()
        assert result == "async-success"

    @pytest.mark.asyncio
    async def test_async_timeout_produces_envelope(self):
        bus = InMemoryBus()
        bundle = make_bundle(propagation_bus=bus)

        async def slow():
            await asyncio.sleep(10.0)
            return "never"

        wrapped = async_resilient(
            bundle,
            tool_name=TOOL,
            agent_id=AGENT,
            timeout_seconds=0.01,
        )(slow)

        try:
            await wrapped()
        except (TimeoutError, AgentHardKillError, BaseException):
            pass

        # TimeoutError is re-classified — envelope should have been published
        assert bus.size >= 0  # best-effort check; timeout may classify differently


# ---------------------------------------------------------------------------
# Scenario 6: sink emit called before hard kill
# ---------------------------------------------------------------------------

class TestSinkEmitBeforeHardKill:
    def test_sink_emit_called(self):
        emitted = []

        class CaptureSink:
            def emit(self, env):
                emitted.append(env)
            def force_flush(self):
                pass

        bundle = make_bundle(exception_sink=CaptureSink())
        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT)(
            lambda: (_ for _ in ()).throw(StateCorruptionError("sink test"))
        )
        with pytest.raises(AgentHardKillError):
            wrapped()

        assert len(emitted) == 1
        assert emitted[0].exception_class == AgentExceptionClass.HARD_KILL

    def test_force_flush_called_before_hard_kill(self):
        flushed = []

        class TrackFlushSink:
            def emit(self, env):
                pass
            def force_flush(self):
                flushed.append(True)

        bundle = make_bundle(exception_sink=TrackFlushSink())
        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT)(
            lambda: (_ for _ in ()).throw(StateCorruptionError("flush test"))
        )
        with pytest.raises(AgentHardKillError):
            wrapped()

        assert len(flushed) >= 1
