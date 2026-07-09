"""resilient() and async_resilient() — tool-level entry points for the resilience stack.

These are the main functional entry points. Every tool and service boundary should
be wrapped with resilient() or async_resilient().

The curried factory API per PEP 612:
    resilient(bundle, *, tool_name, agent_id, ...)(fn)(*args, **kwargs)
    async_resilient(bundle, *, tool_name, agent_id, ...)(fn)(*args, **kwargs)

SDK keyword-only parameters are never placed between P.args and P.kwargs.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, cast

from pydantic import ValidationError

from agentic_exception_sdk.resilience.retry import RetryContext
from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope, SafeContextSnapshot
from agentic_exception_sdk.taxonomy.errors import (
    AgentHardKillError,
    BudgetExhaustedError,
    CompensationPartialFailureError,
    GuardRailViolationError,
    SecurityViolationError,
    StateCorruptionError,
    ToolKindMismatchError,
)

if TYPE_CHECKING:
    from agentic_exception_sdk.bundle import ResilienceBundle

ContextSnapshotInput = SafeContextSnapshot | Mapping[str, Any]

_log = logging.getLogger(__name__)

T = TypeVar("T")
P = ParamSpec("P")

MISSING: object = object()
SAFE_IDENTIFIER_RE: re.Pattern[str] = re.compile(r"^[a-z0-9_-]{1,128}$")

# Re-entrancy guard: set to True while inside a side-effect call (sink/bus/router).
# Prevents recursive emission when a sink itself raises an SDK BaseException.
# Declared with default=False so .get() never raises LookupError.
_in_exception_side_effect: ContextVar[bool] = ContextVar(
    "in_exception_side_effect", default=False
)

# Thread-pool for bounded sync timeout execution (one per interpreter process).
# Not per-bundle — keeping a global bounded pool avoids resource leaks when
# ResilienceBundle instances are created and discarded frequently.
_SYNC_TIMEOUT_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="sdk-resilient-timeout",
)

__all__ = [
    "MISSING",
    "ResilientCallConfig",
    "async_resilient",
    "extend_lineage",
    "force_flush_exception_telemetry",
    "resilient",
]


@dataclass(frozen=True)
class ResilientCallConfig:
    """Configuration for a single resilient wrapper invocation.

    Attributes:
        bundle: The ResilienceBundle providing all components.
        tool_name: Raw tool name (will be canonicalized before use).
        agent_id: Log-safe identifier of the calling agent.
        correlation_id: Optional end-to-end trace ID.
        context_snapshot: Optional agent state snapshot (will be sanitized).
        fallback_value: Optional fallback to return on EXCEPTION or ISSUE recovery.
        timeout_seconds: Optional per-call timeout.
        allow_sync_llm_timeout: If True, allow sync timeout for LLM calls (see docs).
    """

    bundle: ResilienceBundle
    tool_name: str
    agent_id: str
    correlation_id: str | None = None
    context_snapshot: ContextSnapshotInput | None = None
    fallback_value: object = MISSING
    timeout_seconds: float | None = None
    allow_sync_llm_timeout: bool = False


def force_flush_exception_telemetry(sink: object) -> None:
    """Best-effort flush of telemetry before HARD_KILL. Never masks the terminal raise.

    Args:
        sink: The exception event sink; must expose force_flush() if backed by
              a BatchSpanProcessor.
    """
    force_flush = getattr(sink, "force_flush", None)
    if callable(force_flush):
        try:
            force_flush()
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            _log.debug("exception_sink.force_flush failed; ignoring")
        except (
            AgentHardKillError,
            SecurityViolationError,
            StateCorruptionError,
            CompensationPartialFailureError,
        ):
            _log.debug("exception_sink.force_flush failed; ignoring")


def _make_minimal_fallback_envelope(
    agent_id: str,
    correlation_id: str | None,
) -> AgentExceptionEnvelope:
    """Always-succeeding fallback envelope for when primary construction raises ValidationError.

    Preserves agent_id and correlation_id only when they already match the SDK
    log-safe identifier regex; otherwise uses safe defaults. This factory must
    never raise.

    Args:
        agent_id: Agent identifier from the resilient config.
        correlation_id: Trace ID from the resilient config.

    Returns:
        A minimal HARD_KILL / L4_SAFE_ABORT envelope.
    """
    safe_agent_id = agent_id if SAFE_IDENTIFIER_RE.fullmatch(agent_id) else "unknown"
    safe_correlation_id = (
        correlation_id
        if correlation_id is not None and SAFE_IDENTIFIER_RE.fullmatch(correlation_id)
        else None
    )
    return AgentExceptionEnvelope(
        agent_id=safe_agent_id,
        tool_name=None,
        exception_class=AgentExceptionClass.HARD_KILL,
        source=ExceptionSource.ORCHESTRATION,
        error_type="EnvelopeConstructionError",
        message="envelope construction failed",
        context_snapshot=SafeContextSnapshot({}),
        suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
        occurred_at=datetime.now(UTC),
        correlation_id=safe_correlation_id,
        lineage=[],
    )


def _safe_emit(sink: object, envelope: AgentExceptionEnvelope) -> None:
    """Emit envelope to sink with failure isolation and re-entrancy guard.

    Catches Exception only. CancelledError, KeyboardInterrupt, SystemExit, and SDK
    BaseException subclasses are never swallowed here.

    Args:
        sink: The exception event sink.
        envelope: The envelope to emit.
    """
    if _in_exception_side_effect.get():
        _log.debug("skipping sink emit (re-entrancy guard active)")
        return
    token = _in_exception_side_effect.set(True)
    try:
        emit = getattr(sink, "emit", None)
        if callable(emit):
            emit(envelope)
    except Exception:
        _log.debug("exception_sink.emit failed; ignoring")
    finally:
        _in_exception_side_effect.reset(token)


def _safe_publish(bus: object, envelope: AgentExceptionEnvelope) -> None:
    """Publish envelope to bus with failure isolation and re-entrancy guard.

    Args:
        bus: The propagation bus.
        envelope: The envelope to publish.
    """
    if _in_exception_side_effect.get():
        _log.debug("skipping bus publish (re-entrancy guard active)")
        return
    token = _in_exception_side_effect.set(True)
    try:
        publish = getattr(bus, "publish", None)
        if callable(publish):
            publish(envelope)
    except Exception:
        _log.debug("propagation_bus.publish failed; ignoring")
    finally:
        _in_exception_side_effect.reset(token)


def _safe_publish_dlq(dlq: object, envelope: AgentExceptionEnvelope) -> None:
    """Publish a HARD_KILL envelope to the DLQ without masking terminal action."""
    publish = getattr(dlq, "publish", None)
    if not callable(publish):
        return
    try:
        publish(envelope)
    except Exception:
        _log.debug("DLQ publish failed; ignoring")


def _safe_record_exception(metrics_collector: object, envelope: AgentExceptionEnvelope) -> None:
    """Record exception metrics without masking resilience handling."""
    try:
        record_exception = getattr(metrics_collector, "record_exception", None)
        if callable(record_exception):
            record_exception(envelope)
    except Exception:
        _log.debug("metrics_collector.record_exception failed; ignoring")


def _safe_record_hard_kill(metrics_collector: object, agent_id: str) -> None:
    """Record HARD_KILL metrics without masking terminal action."""
    try:
        record_hard_kill = getattr(metrics_collector, "record_hard_kill", None)
        if callable(record_hard_kill):
            record_hard_kill(agent_id)
    except Exception:
        _log.debug("metrics_collector.record_hard_kill failed; ignoring")


def _safe_record_retry(metrics_collector: object, agent_id: str) -> None:
    """Record retry metrics without masking retry handling."""
    try:
        record_retry = getattr(metrics_collector, "record_retry", None)
        if callable(record_retry):
            record_retry(agent_id)
    except Exception:
        _log.debug("metrics_collector.record_retry failed; ignoring")


def _safe_record_budget_exhausted(metrics_collector: object) -> None:
    """Record budget exhaustion metrics without masking terminal action."""
    try:
        record_budget_exhausted = getattr(
            metrics_collector,
            "record_budget_exhausted",
            None,
        )
        if callable(record_budget_exhausted):
            record_budget_exhausted()
    except Exception:
        _log.debug("metrics_collector.record_budget_exhausted failed; ignoring")


def _safe_record_latency(
    latency_histogram: object,
    elapsed_seconds: float,
    *,
    agent_id: str,
    tool_name: str | None,
    exception_class: AgentExceptionClass | None = None,
) -> None:
    """Record tool-call latency without masking resilience handling."""
    try:
        record = getattr(latency_histogram, "record", None)
        if callable(record):
            attributes = {
                "agent_id": agent_id,
                "tool_name": tool_name or "",
            }
            if exception_class is not None:
                attributes["exception_class"] = exception_class.value
            record(elapsed_seconds, attributes=attributes)
    except Exception:
        _log.debug("latency_histogram.record failed; ignoring")


def _safe_route(router: object, envelope: AgentExceptionEnvelope) -> Any:
    """Route envelope through escalation router with failure isolation.

    Args:
        router: The escalation router.
        envelope: The envelope to route.

    Returns:
        RecoveryDirective | None — None if routing fails.
    """
    if _in_exception_side_effect.get():
        _log.debug("skipping escalation route (re-entrancy guard active)")
        return None
    token = _in_exception_side_effect.set(True)
    try:
        route = getattr(router, "route", None)
        if callable(route):
            return route(envelope)
        return None
    except Exception:
        _log.debug("escalation_router.route failed; ignoring")
        return None
    finally:
        _in_exception_side_effect.reset(token)


def _build_envelope(
    exc: BaseException,
    config: ResilientCallConfig,
    canonical_tool_name: str,
) -> AgentExceptionEnvelope:
    """Build the exception envelope with sanitized message and context.

    If Pydantic raises a ValidationError during construction, falls back to the
    minimal fallback envelope. Never logs or emits raw ValidationError.errors().

    Args:
        exc: The caught exception.
        config: The resilient call configuration.
        canonical_tool_name: The already-canonicalized tool name.

    Returns:
        A valid AgentExceptionEnvelope (either primary or minimal fallback).
    """
    bundle = config.bundle
    exc_class, source, level = bundle.classifier.classify(exc)
    safe_message = bundle.trust_boundary.safe_exception_message(exc)
    context_mapping = _context_snapshot_mapping(config.context_snapshot)
    safe_context = bundle.trust_boundary.sanitize_context_snapshot(context_mapping)

    try:
        envelope = AgentExceptionEnvelope(
            agent_id=config.agent_id,
            tool_name=canonical_tool_name,
            exception_class=exc_class,
            source=source,
            error_type=type(exc).__name__,
            message=safe_message,
            context_snapshot=safe_context,
            suggested_recovery=level,
            occurred_at=datetime.now(UTC),
            correlation_id=config.correlation_id,
            lineage=[config.agent_id],
        )
        _safe_record_exception(bundle.metrics_collector, envelope)
        return envelope
    except ValidationError:
        _log.debug("envelope construction ValidationError; using minimal fallback envelope")
        envelope = _make_minimal_fallback_envelope(config.agent_id, config.correlation_id)
        _safe_record_exception(bundle.metrics_collector, envelope)
        return envelope


def _context_snapshot_mapping(
    context_snapshot: ContextSnapshotInput | None,
) -> Mapping[str, Any] | None:
    if context_snapshot is None:
        return None
    if isinstance(context_snapshot, SafeContextSnapshot):
        return context_snapshot.root
    return context_snapshot


def _execute_resilient_sync(
    fn: Callable[P, T],
    config: ResilientCallConfig,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Core synchronous execution pipeline.

    Execution order:
    1. Canonicalize tool name + guard rails check
    2. Budget consume_call()
    3. Reject coroutine functions
    4. Timeout_seconds + allow_sync_llm_timeout check
    5. Circuit breaker wrapping retry wrapping fn
    6. Output validation gate
    7. Exception classification, envelope, emit, publish, route, terminal action

    Args:
        fn: The sync callable to execute.
        config: The resilient call configuration.
        *args: Positional arguments forwarded to fn.
        **kwargs: Keyword arguments forwarded to fn.

    Returns:
        The return value of fn, or fallback_value, or resume_state.

    Raises:
        AgentHardKillError: For HARD_KILL class failures.
        ToolKindMismatchError: If fn is a coroutine function.
        Exception: Re-raises EXCEPTION/ISSUE class failures when no fallback is set.
    """
    bundle = config.bundle

    # Steps 3 & 4: callable-kind checks are programming errors — must propagate before try
    if inspect.iscoroutinefunction(fn):
        raise ToolKindMismatchError(
            "resilient() received a coroutine function; use async_resilient() instead"
        )
    if config.timeout_seconds is not None and not config.allow_sync_llm_timeout:
        raise ToolKindMismatchError(
            "timeout_seconds is set on resilient() but allow_sync_llm_timeout=False. "
            "LLM-facing timed calls must use async_resilient() with asyncio.timeout()."
        )

    # canonical_tool_name must be set before the try block so except clauses can use it
    canonical_tool_name = bundle.trust_boundary.canonicalize_tool_name(config.tool_name)
    started_at = time.perf_counter()

    try:
        # 1. Guard rails (inside try so violations produce AgentHardKillError envelopes)
        bundle.guard_rails.check(canonical_tool_name)

        # 2. Budget (inside try so exhaustion produces AgentHardKillError envelopes)
        bundle.agent_budget.consume_call()
        # 5. Execute via circuit breaker -> retry policy -> fn
        if config.timeout_seconds is not None and config.allow_sync_llm_timeout:
            def _run_with_timeout() -> T:
                future: Future[T] = _SYNC_TIMEOUT_EXECUTOR.submit(fn, *args, **kwargs)
                try:
                    return future.result(timeout=config.timeout_seconds)
                except Exception:
                    raise

            def _call_fn() -> T:
                return _run_with_timeout()
        else:
            def _call_fn() -> T:
                return fn(*args, **kwargs)

        def _retry_fn() -> T:
            context = RetryContext(
                correlation_id=config.correlation_id,
                agent_id=config.agent_id,
                tool_name=canonical_tool_name,
                on_retry=lambda: _safe_record_retry(
                    bundle.metrics_collector,
                    config.agent_id,
                ),
            )
            return cast("T", bundle.retry_policy.execute(_call_fn, context=context))

        result = cast("T", bundle.circuit_breaker.call(_retry_fn))

        # 6. Output validation
        validated = cast("T", bundle.output_validation_gate.validate(result))
        _safe_record_latency(
            bundle._latency_histogram,
            time.perf_counter() - started_at,
            agent_id=config.agent_id,
            tool_name=canonical_tool_name,
        )
        return validated

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise

    except BaseExceptionGroup as group_exc:
        # Split control-flow vs failure branches
        control_types = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
        control, failures = group_exc.split(control_types)

        if failures is not None:
            # Classify and process the failure branch
            envelope = _build_envelope(failures, config, canonical_tool_name)
            _safe_record_latency(
                bundle._latency_histogram,
                time.perf_counter() - started_at,
                agent_id=config.agent_id,
                tool_name=canonical_tool_name,
                exception_class=envelope.exception_class,
            )
            _safe_emit(bundle.exception_sink, envelope)
            _safe_publish(bundle.propagation_bus, envelope)
            exc_class = envelope.exception_class
            _handle_tier(exc_class, envelope, config, bundle)

        if control is not None:
            raise control from None

        raise  # pragma: no cover

    except (
        SecurityViolationError,
        BudgetExhaustedError,
        StateCorruptionError,
        CompensationPartialFailureError,
        GuardRailViolationError,
    ) as sdk_exc:
        envelope = _build_envelope(sdk_exc, config, canonical_tool_name)
        _safe_record_latency(
            bundle._latency_histogram,
            time.perf_counter() - started_at,
            agent_id=config.agent_id,
            tool_name=canonical_tool_name,
            exception_class=envelope.exception_class,
        )
        _safe_emit(bundle.exception_sink, envelope)
        _safe_publish(bundle.propagation_bus, envelope)
        _safe_publish_dlq(bundle.dlq, envelope)
        _safe_route(bundle.escalation_router, envelope)
        if isinstance(sdk_exc, BudgetExhaustedError):
            _safe_record_budget_exhausted(bundle.metrics_collector)
        _safe_record_hard_kill(bundle.metrics_collector, config.agent_id)
        force_flush_exception_telemetry(bundle.exception_sink)
        raise AgentHardKillError(envelope) from None

    except Exception as exc:
        envelope = _build_envelope(exc, config, canonical_tool_name)
        _safe_record_latency(
            bundle._latency_histogram,
            time.perf_counter() - started_at,
            agent_id=config.agent_id,
            tool_name=canonical_tool_name,
            exception_class=envelope.exception_class,
        )
        _safe_emit(bundle.exception_sink, envelope)
        _safe_publish(bundle.propagation_bus, envelope)
        return cast("T", _handle_tier(envelope.exception_class, envelope, config, bundle))


def _handle_tier(
    exc_class: AgentExceptionClass,
    envelope: AgentExceptionEnvelope,
    config: ResilientCallConfig,
    bundle: ResilienceBundle,
) -> Any:
    """Apply tier-specific terminal action after emit and publish.

    Args:
        exc_class: The classified exception tier.
        envelope: The built exception envelope.
        config: The resilient call configuration.
        bundle: The ResilienceBundle providing routing and telemetry.

    Returns:
        fallback_value or resume_state when recovery is possible.

    Raises:
        AgentHardKillError: For HARD_KILL tier.
        Exception: Re-raises for EXCEPTION/ISSUE tiers when no fallback is set.
    """
    if exc_class == AgentExceptionClass.EXCEPTION:
        policy_result = _apply_recovery_policy(bundle, envelope)
        if policy_result is not MISSING:
            return policy_result
        if config.fallback_value is not MISSING:
            return config.fallback_value
        raise  # re-raise original

    if exc_class == AgentExceptionClass.ISSUE:
        policy_result = _apply_recovery_policy(bundle, envelope)
        if policy_result is not MISSING:
            return policy_result
        directive = _safe_route(bundle.escalation_router, envelope)
        if directive is not None:
            if directive.action == "resume" and directive.resume_state is not None:
                return directive.resume_state
            if directive.action == "abort":
                # Promote ISSUE envelope to a fresh HARD_KILL envelope
                hk_envelope = AgentExceptionEnvelope(
                    **{
                        k: v for k, v in envelope.model_dump().items()
                        if k not in {"exception_class", "suggested_recovery", "sdk_version"}
                    },
                    exception_class=AgentExceptionClass.HARD_KILL,
                    suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
                )
                _safe_publish_dlq(bundle.dlq, hk_envelope)
                _safe_record_hard_kill(bundle.metrics_collector, config.agent_id)
                force_flush_exception_telemetry(bundle.exception_sink)
                raise AgentHardKillError(hk_envelope)
        if config.fallback_value is not MISSING:
            return config.fallback_value
        raise  # re-raise original

    # HARD_KILL
    _safe_route(bundle.escalation_router, envelope)
    _safe_publish_dlq(bundle.dlq, envelope)
    _safe_record_hard_kill(bundle.metrics_collector, config.agent_id)
    force_flush_exception_telemetry(bundle.exception_sink)
    raise AgentHardKillError(envelope)


def _apply_recovery_policy(
    bundle: ResilienceBundle,
    envelope: AgentExceptionEnvelope,
) -> object:
    """Apply optional post-classification recovery policy.

    Only ``RecoveryDirective(action="resume")`` with a non-None ``resume_state``
    overrides default SDK behavior. ``None``, ``escalate``, ``abort``, and
    ``resume`` without state deliberately fall through to existing fallback,
    escalation-router, re-raise, or ISSUE-to-HARD_KILL promotion logic.
    """
    recovery_policy = getattr(bundle, "recovery_policy", None)
    if recovery_policy is None:
        return MISSING

    directive = recovery_policy.recover(envelope)
    if directive is None:
        return MISSING
    if directive.action == "resume" and directive.resume_state is not None:
        return directive.resume_state
    return MISSING


async def _execute_resilient_async(
    fn: Callable[P, Awaitable[T]],
    config: ResilientCallConfig,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Core asynchronous execution pipeline.

    Same semantics as _execute_resilient_sync but for async callables.
    Async timeout uses asyncio.timeout(), NOT asyncio.wait_for().
    Async circuit breaker and bus/sink calls use async-safe variants when available.

    Args:
        fn: The async callable to execute.
        config: The resilient call configuration.
        *args: Positional arguments forwarded to fn.
        **kwargs: Keyword arguments forwarded to fn.

    Returns:
        The return value of fn, or fallback_value, or resume_state.

    Raises:
        AgentHardKillError: For HARD_KILL class failures.
        ToolKindMismatchError: If fn is not a coroutine function.
        Exception: Re-raises EXCEPTION/ISSUE class failures when no fallback is set.
    """
    bundle = config.bundle

    # Step 3: callable-kind check is a programming error — propagates before try
    if not inspect.iscoroutinefunction(fn):
        raise ToolKindMismatchError(
            "async_resilient() received a non-coroutine function; use resilient() instead"
        )

    canonical_tool_name = bundle.trust_boundary.canonicalize_tool_name(config.tool_name)
    started_at = time.perf_counter()

    try:
        # 1. Guard rails (inside try so violations produce AgentHardKillError envelopes)
        bundle.guard_rails.check(canonical_tool_name)

        # 2. Budget (inside try so exhaustion produces AgentHardKillError envelopes)
        bundle.agent_budget.consume_call()
        async def _call_fn() -> T:
            if config.timeout_seconds is not None:
                async with asyncio.timeout(config.timeout_seconds):
                    return await fn(*args, **kwargs)
            return await fn(*args, **kwargs)

        async def _retry_fn() -> T:
            context = RetryContext(
                correlation_id=config.correlation_id,
                agent_id=config.agent_id,
                tool_name=canonical_tool_name,
                on_retry=lambda: _safe_record_retry(
                    bundle.metrics_collector,
                    config.agent_id,
                ),
            )
            return cast("T", await bundle.retry_policy.async_execute(_call_fn, context=context))

        # Use async circuit breaker if available
        async_cb = getattr(bundle, "async_circuit_breaker", None)
        if async_cb is not None:
            result = cast("T", await async_cb.call(_retry_fn))
        else:
            result = await _retry_fn()

        # 6. Output validation
        validated = cast("T", bundle.output_validation_gate.validate(result))
        _safe_record_latency(
            bundle._latency_histogram,
            time.perf_counter() - started_at,
            agent_id=config.agent_id,
            tool_name=canonical_tool_name,
        )
        return validated

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise

    except BaseExceptionGroup as group_exc:
        control_types = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
        control, failures = group_exc.split(control_types)

        if failures is not None:
            envelope = _build_envelope(failures, config, canonical_tool_name)
            _safe_record_latency(
                bundle._latency_histogram,
                time.perf_counter() - started_at,
                agent_id=config.agent_id,
                tool_name=canonical_tool_name,
                exception_class=envelope.exception_class,
            )
            _safe_emit(bundle.exception_sink, envelope)
            _safe_publish(bundle.propagation_bus, envelope)
            _handle_tier(envelope.exception_class, envelope, config, bundle)

        if control is not None:
            raise control from None

        raise  # pragma: no cover

    except (
        SecurityViolationError,
        BudgetExhaustedError,
        StateCorruptionError,
        CompensationPartialFailureError,
        GuardRailViolationError,
    ) as sdk_exc:
        envelope = _build_envelope(sdk_exc, config, canonical_tool_name)
        _safe_record_latency(
            bundle._latency_histogram,
            time.perf_counter() - started_at,
            agent_id=config.agent_id,
            tool_name=canonical_tool_name,
            exception_class=envelope.exception_class,
        )
        _safe_emit(bundle.exception_sink, envelope)
        _safe_publish(bundle.propagation_bus, envelope)
        _safe_publish_dlq(bundle.dlq, envelope)
        _safe_route(bundle.escalation_router, envelope)
        if isinstance(sdk_exc, BudgetExhaustedError):
            _safe_record_budget_exhausted(bundle.metrics_collector)
        _safe_record_hard_kill(bundle.metrics_collector, config.agent_id)
        force_flush_exception_telemetry(bundle.exception_sink)
        raise AgentHardKillError(envelope) from None

    except Exception as exc:
        envelope = _build_envelope(exc, config, canonical_tool_name)
        _safe_record_latency(
            bundle._latency_histogram,
            time.perf_counter() - started_at,
            agent_id=config.agent_id,
            tool_name=canonical_tool_name,
            exception_class=envelope.exception_class,
        )
        _safe_emit(bundle.exception_sink, envelope)
        _safe_publish(bundle.propagation_bus, envelope)
        return cast("T", _handle_tier(envelope.exception_class, envelope, config, bundle))


def resilient(
    bundle: ResilienceBundle,
    *,
    tool_name: str,
    agent_id: str,
    correlation_id: str | None = None,
    context_snapshot: ContextSnapshotInput | None = None,
    fallback_value: object = MISSING,
    timeout_seconds: float | None = None,
    allow_sync_llm_timeout: bool = False,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Return a ParamSpec-preserving synchronous resilience wrapper.

    Wraps any sync callable through the full resilience pipeline:
    guard rails -> budget -> circuit breaker -> retry -> output validation ->
    classification -> envelope -> sink -> bus -> escalation routing.

    Warning: Retrying non-idempotent side-effecting tools is unsafe unless an
    idempotency key or compensating transaction is supplied.

    Args:
        bundle: The ResilienceBundle providing all components.
        tool_name: Raw tool name (canonicalized internally).
        agent_id: Log-safe identifier of the calling agent.
        correlation_id: Optional end-to-end trace ID.
        context_snapshot: Optional agent state for envelope context.
        fallback_value: Optional fallback returned on EXCEPTION or ISSUE recovery.
        timeout_seconds: Optional per-call timeout using a bounded ThreadPoolExecutor.
            Requires allow_sync_llm_timeout=True for LLM-facing calls.
        allow_sync_llm_timeout: If True, allow sync timeout for LLM calls.
            Note: the leaked thread cannot be cancelled and may continue spending
            tokens and generating telemetry after the wrapper returns.

    Returns:
        A decorator that wraps the callable with the resilience pipeline.

    Raises:
        ToolKindMismatchError: If the decorated function is a coroutine function.
        ToolKindMismatchError: If timeout_seconds is set and allow_sync_llm_timeout is False.
    """
    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        if inspect.iscoroutinefunction(fn):
            raise ToolKindMismatchError(
                "resilient() received a coroutine function; use async_resilient() instead"
            )

        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            config = ResilientCallConfig(
                bundle=bundle,
                tool_name=tool_name,
                agent_id=agent_id,
                correlation_id=correlation_id,
                context_snapshot=context_snapshot,
                fallback_value=fallback_value,
                timeout_seconds=timeout_seconds,
                allow_sync_llm_timeout=allow_sync_llm_timeout,
            )
            return _execute_resilient_sync(fn, config, *args, **kwargs)

        return wrapper

    return decorator


def async_resilient(
    bundle: ResilienceBundle,
    *,
    tool_name: str,
    agent_id: str,
    correlation_id: str | None = None,
    context_snapshot: ContextSnapshotInput | None = None,
    fallback_value: object = MISSING,
    timeout_seconds: float | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Return a ParamSpec-preserving asynchronous resilience wrapper.

    Same semantics as resilient() but for async callables.
    Async timeout uses asyncio.timeout() — NOT asyncio.wait_for().

    Warning: Retrying non-idempotent side-effecting tools is unsafe unless an
    idempotency key or compensating transaction is supplied.

    Args:
        bundle: The ResilienceBundle providing all components.
        tool_name: Raw tool name (canonicalized internally).
        agent_id: Log-safe identifier of the calling agent.
        correlation_id: Optional end-to-end trace ID.
        context_snapshot: Optional agent state for envelope context.
        fallback_value: Optional fallback returned on EXCEPTION or ISSUE recovery.
        timeout_seconds: Optional per-call timeout using asyncio.timeout().

    Returns:
        A decorator that wraps the async callable with the resilience pipeline.

    Raises:
        ToolKindMismatchError: If the decorated function is not a coroutine function.
    """
    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        if not inspect.iscoroutinefunction(fn):
            raise ToolKindMismatchError(
                "async_resilient() received a non-coroutine function; use resilient() instead"
            )

        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            config = ResilientCallConfig(
                bundle=bundle,
                tool_name=tool_name,
                agent_id=agent_id,
                correlation_id=correlation_id,
                context_snapshot=context_snapshot,
                fallback_value=fallback_value,
                timeout_seconds=timeout_seconds,
            )
            return await _execute_resilient_async(fn, config, *args, **kwargs)

        return wrapper

    return decorator


def extend_lineage(
    envelope: AgentExceptionEnvelope,
    next_agent_id: str,
) -> AgentExceptionEnvelope:
    """Extend envelope lineage by one agent hop for multi-agent propagation.

    Lineage is capped at 64 hops. If adding a hop would exceed the cap, a fresh
    HARD_KILL / L4_SAFE_ABORT envelope is created with error_type="LineageCapExceededError".
    Do not use model_copy(update=...) — Pydantic v2 skips validators on that path.

    Args:
        envelope: The current exception envelope.
        next_agent_id: The identifier of the next agent receiving the envelope.

    Returns:
        A new AgentExceptionEnvelope with next_agent_id appended to lineage,
        or a fresh HARD_KILL envelope if the 64-hop cap is exceeded.
    """
    if len(envelope.lineage) >= 64:
        return AgentExceptionEnvelope(
            agent_id=envelope.agent_id,
            tool_name=envelope.tool_name,
            exception_class=AgentExceptionClass.HARD_KILL,
            source=ExceptionSource.ORCHESTRATION,
            error_type="LineageCapExceededError",
            message="lineage cap exceeded at hop 64",
            context_snapshot=SafeContextSnapshot({}),
            suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
            occurred_at=datetime.now(UTC),
            correlation_id=envelope.correlation_id,
            lineage=[*envelope.lineage, next_agent_id],
        )
    return AgentExceptionEnvelope(
        **{k: v for k, v in envelope.model_dump().items() if k not in {"lineage", "sdk_version"}},
        lineage=[*envelope.lineage, next_agent_id],
    )
