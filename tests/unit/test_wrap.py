"""Tests for resilient() and async_resilient() wrappers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agentic_exception_sdk import (
    AgentBudget,
    AgentExceptionClass,
    AgentHardKillError,
    BudgetWatchdog,
    EscalationLevel,
    ExceptionSource,
    ExponentialBackoffRetry,
    InMemoryMetricsCollector,
    NoOpMetricsCollector,
    NoOpResilienceBundle,
    ResilienceBundle,
    SafeContextSnapshot,
    async_resilient,
    extend_lineage,
    resilient,
)
from agentic_exception_sdk.propagation.bus import InMemoryBus
from agentic_exception_sdk.propagation.dlq import InMemoryDLQ
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope
from agentic_exception_sdk.taxonomy.errors import (
    SecurityViolationError,
    StateCorruptionError,
    ToolKindMismatchError,
)

AGENT_ID = "test-agent"
TOOL = "test-tool"
CORR = "corr-01"


class FakeHistogram:
    def __init__(self):
        self.records = []

    def record(self, elapsed, *, attributes):
        self.records.append((elapsed, attributes))


class FakeMeter:
    def __init__(self):
        self.histogram = FakeHistogram()
        self.create_histogram_calls = []

    def create_histogram(self, name, *, unit, description):
        self.create_histogram_calls.append((name, unit, description))
        return self.histogram


class FakeMeterProvider:
    def __init__(self):
        self.meter = FakeMeter()
        self.get_meter_calls = []

    def get_meter(self, name):
        self.get_meter_calls.append(name)
        return self.meter


def make_bundle(**kwargs) -> ResilienceBundle:
    b = NoOpResilienceBundle()
    for k, v in kwargs.items():
        setattr(b, k, v)
    return b


# ---------------------------------------------------------------------------
# resilient() — basic behaviour
# ---------------------------------------------------------------------------

class TestResilientBasic:
    def test_returns_value(self):
        bundle = make_bundle()
        fn = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(lambda: 42)
        assert fn() == 42

    def test_publishes_envelope_on_exception(self):
        bus = InMemoryBus()
        bundle = make_bundle(propagation_bus=bus)

        def fail():
            raise TimeoutError("timeout")

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID, fallback_value="fb")(fail)
        result = wrapped()
        assert result == "fb"
        assert bus.size == 1

    def test_reraises_when_no_fallback(self):
        bundle = make_bundle()

        def fail():
            raise TimeoutError("timeout")

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(fail)
        with pytest.raises(TimeoutError):
            wrapped()

    def test_hard_kill_raises_agent_hard_kill_error(self):
        bundle = make_bundle()

        def corrupt():
            raise StateCorruptionError("corrupt state")

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(corrupt)
        with pytest.raises(AgentHardKillError):
            wrapped()

    def test_hard_kill_publishes_to_dlq(self):
        dlq = InMemoryDLQ()
        bundle = make_bundle(dlq=dlq)

        def corrupt():
            raise StateCorruptionError("corrupt state")

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(corrupt)
        with pytest.raises(AgentHardKillError):
            wrapped()

        envelopes = dlq.drain()
        assert len(envelopes) == 1
        assert envelopes[0].exception_class == AgentExceptionClass.HARD_KILL

    def test_dlq_publish_failure_does_not_mask_hard_kill(self):
        class FailingDLQ:
            def publish(self, envelope):
                raise RuntimeError("dlq unavailable")

        bundle = make_bundle(dlq=FailingDLQ())

        def corrupt():
            raise StateCorruptionError("corrupt state")

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(corrupt)
        with pytest.raises(AgentHardKillError):
            wrapped()

    def test_hard_kill_not_caught_by_except_exception(self):
        bundle = make_bundle()

        def corrupt():
            raise StateCorruptionError("corrupt state")

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(corrupt)
        caught_by_exception = False
        try:
            wrapped()
        except Exception:
            caught_by_exception = True
        except BaseException:
            pass
        assert not caught_by_exception

    def test_coroutine_function_rejected(self):
        bundle = make_bundle()
        async def async_fn():
            return 1
        with pytest.raises(ToolKindMismatchError):
            resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(async_fn)

    def test_fallback_returned_on_exception_class(self):
        bundle = make_bundle()

        def fail():
            raise TimeoutError("timeout")

        wrapped = resilient(
            bundle, tool_name=TOOL, agent_id=AGENT_ID, fallback_value="DEFAULT"
        )(fail)
        assert wrapped() == "DEFAULT"

    def test_envelope_published_to_bus(self):
        bus = InMemoryBus()
        bundle = make_bundle(propagation_bus=bus)

        def fail():
            raise TimeoutError("timeout")

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID, fallback_value="fb")(fail)
        wrapped()
        envelopes = bus.drain()
        assert len(envelopes) == 1
        assert envelopes[0].error_type == "TimeoutError"

    def test_envelope_has_correct_agent_id(self):
        bus = InMemoryBus()
        bundle = make_bundle(propagation_bus=bus)

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID, fallback_value="fb")(
            lambda: (_ for _ in ()).throw(TimeoutError("t"))
        )
        wrapped()
        envelopes = bus.drain()
        assert envelopes[0].agent_id == AGENT_ID

    def test_default_bundle_uses_noop_metrics_collector(self):
        bundle = ResilienceBundle()

        assert isinstance(bundle.metrics_collector, NoOpMetricsCollector)

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID, fallback_value="fb")(
            lambda: (_ for _ in ()).throw(TimeoutError("t"))
        )
        assert wrapped() == "fb"

    def test_metrics_collector_records_exception(self):
        metrics = InMemoryMetricsCollector()
        bundle = make_bundle(metrics_collector=metrics)

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID, fallback_value="fb")(
            lambda: (_ for _ in ()).throw(TimeoutError("t"))
        )
        assert wrapped() == "fb"

        assert metrics.exception_counts["exception:L0_SELF_RETRY:tool"] == 1

    def test_metrics_collector_exception_does_not_mask_resilience_flow(self):
        class FailingMetricsCollector:
            def record_exception(self, envelope):
                raise RuntimeError("metrics unavailable")

            def record_hard_kill(self, agent_id=None):
                raise RuntimeError("metrics unavailable")

            def record_retry(self, agent_id=None):
                raise RuntimeError("metrics unavailable")

            def record_budget_exhausted(self):
                raise RuntimeError("metrics unavailable")

        bundle = make_bundle(metrics_collector=FailingMetricsCollector())

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID, fallback_value="fb")(
            lambda: (_ for _ in ()).throw(TimeoutError("t"))
        )

        assert wrapped() == "fb"

    def test_metrics_collector_records_retries(self):
        metrics = InMemoryMetricsCollector()
        retry = ExponentialBackoffRetry(
            max_attempts=3,
            base_delay_seconds=0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
        )
        bundle = make_bundle(metrics_collector=metrics, retry_policy=retry)
        calls = [0]

        def flaky():
            calls[0] += 1
            if calls[0] < 3:
                raise TimeoutError("retry")
            return "ok"

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(flaky)

        assert wrapped() == "ok"
        assert calls[0] == 3
        assert metrics.retry_total == 2
        assert metrics.exception_counts == {}

    def test_meter_provider_creates_latency_histogram_once(self):
        provider = FakeMeterProvider()

        bundle = ResilienceBundle(meter_provider=provider)

        assert provider.get_meter_calls == ["agentic_exception_sdk"]
        assert provider.meter.create_histogram_calls == [
            (
                "agent_tool_call_duration_seconds",
                "s",
                "Per-call tool duration by agent and tool name",
            )
        ]
        assert bundle._latency_histogram is provider.meter.histogram

    def test_latency_histogram_records_success(self):
        provider = FakeMeterProvider()
        bundle = ResilienceBundle(meter_provider=provider)

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(lambda: "ok")

        assert wrapped() == "ok"
        assert len(provider.meter.histogram.records) == 1
        elapsed, attributes = provider.meter.histogram.records[0]
        assert elapsed >= 0
        assert attributes == {"agent_id": AGENT_ID, "tool_name": TOOL}

    def test_latency_histogram_records_failure_exception_class(self):
        provider = FakeMeterProvider()
        bundle = ResilienceBundle(meter_provider=provider)

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID, fallback_value="fb")(
            lambda: (_ for _ in ()).throw(TimeoutError("t"))
        )

        assert wrapped() == "fb"
        assert len(provider.meter.histogram.records) == 1
        _, attributes = provider.meter.histogram.records[0]
        assert attributes == {
            "agent_id": AGENT_ID,
            "tool_name": TOOL,
            "exception_class": "exception",
        }

    def test_metrics_collector_records_hard_kill(self):
        metrics = InMemoryMetricsCollector()
        bundle = make_bundle(metrics_collector=metrics)

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(
            lambda: (_ for _ in ()).throw(StateCorruptionError("corrupt state"))
        )

        with pytest.raises(AgentHardKillError):
            wrapped()

        assert metrics.exception_counts["hard_kill:L4_SAFE_ABORT:orchestration"] == 1
        assert metrics.hard_kill_total == 1

    def test_metrics_collector_records_budget_exhausted(self):
        metrics = InMemoryMetricsCollector()
        watchdog = BudgetWatchdog(AgentBudget(max_tool_calls=0))
        bundle = make_bundle(metrics_collector=metrics, agent_budget=watchdog)

        wrapped = resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(lambda: "unused")

        with pytest.raises(AgentHardKillError):
            wrapped()

        assert metrics.exception_counts["hard_kill:L4_SAFE_ABORT:orchestration"] == 1
        assert metrics.hard_kill_total == 1
        assert metrics.budget_exhausted_total == 1


# ---------------------------------------------------------------------------
# async_resilient() — basic behaviour
# ---------------------------------------------------------------------------

class TestAsyncResilientBasic:
    @pytest.mark.asyncio
    async def test_returns_value(self):
        bundle = make_bundle()
        async def fn():
            return 99
        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(fn)
        assert await wrapped() == 99

    @pytest.mark.asyncio
    async def test_fallback_on_timeout_exception(self):
        bus = InMemoryBus()
        bundle = make_bundle(propagation_bus=bus)

        async def fail():
            raise TimeoutError("async timeout")

        wrapped = async_resilient(
            bundle, tool_name=TOOL, agent_id=AGENT_ID, fallback_value="async-fb"
        )(fail)
        result = await wrapped()
        assert result == "async-fb"

    @pytest.mark.asyncio
    async def test_hard_kill_raised(self):
        bundle = make_bundle()

        async def corrupt():
            raise SecurityViolationError("injection")

        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(corrupt)
        with pytest.raises(AgentHardKillError):
            await wrapped()

    @pytest.mark.asyncio
    async def test_async_hard_kill_publishes_to_dlq(self):
        dlq = InMemoryDLQ()
        bundle = make_bundle(dlq=dlq)

        async def corrupt():
            raise SecurityViolationError("injection")

        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(corrupt)
        with pytest.raises(AgentHardKillError):
            await wrapped()

        envelopes = dlq.drain()
        assert len(envelopes) == 1
        assert envelopes[0].exception_class == AgentExceptionClass.HARD_KILL

    @pytest.mark.asyncio
    async def test_sync_function_rejected(self):
        bundle = make_bundle()
        def sync_fn():
            return 1
        with pytest.raises(ToolKindMismatchError):
            async_resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(sync_fn)

    @pytest.mark.asyncio
    async def test_asyncio_timeout_raises_cancelled(self):
        bundle = make_bundle()

        async def slow():
            await asyncio.sleep(10.0)
            return "done"

        wrapped = async_resilient(
            bundle, tool_name=TOOL, agent_id=AGENT_ID, timeout_seconds=0.01
        )(slow)
        with pytest.raises((asyncio.TimeoutError, AgentHardKillError)):
            await wrapped()

    @pytest.mark.asyncio
    async def test_async_metrics_collector_records_retries(self):
        metrics = InMemoryMetricsCollector()
        retry = ExponentialBackoffRetry(
            max_attempts=3,
            base_delay_seconds=0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
        )
        bundle = make_bundle(metrics_collector=metrics, retry_policy=retry)
        calls = [0]

        async def flaky():
            calls[0] += 1
            if calls[0] < 3:
                raise TimeoutError("async retry")
            return "ok"

        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT_ID)(flaky)

        assert await wrapped() == "ok"
        assert calls[0] == 3
        assert metrics.retry_total == 2
        assert metrics.exception_counts == {}


# ---------------------------------------------------------------------------
# extend_lineage tests
# ---------------------------------------------------------------------------

class TestExtendLineage:
    def _make_env(self, lineage: list[str]) -> AgentExceptionEnvelope:
        return AgentExceptionEnvelope(
            agent_id="origin-agent",
            exception_class=AgentExceptionClass.EXCEPTION,
            source=ExceptionSource.TOOL,
            error_type="Error",
            message="msg",
            context_snapshot=SafeContextSnapshot({}),
            suggested_recovery=EscalationLevel.L0_SELF_RETRY,
            occurred_at=datetime.now(UTC),
            lineage=lineage,
        )

    def test_appends_agent_id(self):
        env = self._make_env(["agent-a"])
        env2 = extend_lineage(env, "agent-b")
        assert env2.lineage == ["agent-a", "agent-b"]

    def test_original_envelope_unchanged(self):
        env = self._make_env(["agent-a"])
        extend_lineage(env, "agent-b")
        assert env.lineage == ["agent-a"]

    def test_lineage_cap_at_64_produces_hard_kill(self):
        lineage = [f"agent-{i}" for i in range(64)]
        env = self._make_env(lineage)
        result = extend_lineage(env, "agent-overflow")
        assert result.exception_class == AgentExceptionClass.HARD_KILL
        assert result.error_type == "LineageCapExceededError"

    def test_exactly_63_hops_still_extends(self):
        lineage = [f"agent-{i}" for i in range(63)]
        env = self._make_env(lineage)
        result = extend_lineage(env, "agent-64")
        assert len(result.lineage) == 64
        assert result.exception_class == AgentExceptionClass.EXCEPTION


# ---------------------------------------------------------------------------
# guard_rails integration
# ---------------------------------------------------------------------------

class TestGuardRailsIntegration:
    def test_guard_rail_violation_raises_hard_kill(self):
        from agentic_exception_sdk.validation.guard_rails import (
            AllowlistedOperations,
            GuardRailPolicy,
        )
        from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator

        tb = TrustBoundaryValidator()
        ops = AllowlistedOperations.from_iterable(["allowed-tool"], trust_boundary=tb)
        policy = GuardRailPolicy(trust_boundary=tb, allowlisted_operations=ops)
        bundle = make_bundle(guard_rails=policy)

        wrapped = resilient(bundle, tool_name="forbidden-tool", agent_id=AGENT_ID)(lambda: 1)
        with pytest.raises(AgentHardKillError):
            wrapped()
