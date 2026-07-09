"""Parallel call aggregation for independent agent/tool calls."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agentic_exception_sdk.bundle import ResilienceBundle
from agentic_exception_sdk.resilience.wrap import async_resilient, resilient
from agentic_exception_sdk.taxonomy.enums import AgentExceptionClass
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope
from agentic_exception_sdk.taxonomy.errors import AgentHardKillError

__all__ = [
    "AgentOutcome",
    "FanOutOutcomeEnvelope",
    "call_parallel",
]

_SEVERITY: dict[AgentExceptionClass, int] = {
    AgentExceptionClass.EXCEPTION: 0,
    AgentExceptionClass.ISSUE: 1,
    AgentExceptionClass.HARD_KILL: 2,
}

_FAILED_CALL = object()


class AgentOutcome(BaseModel):
    """Outcome for one call executed by call_parallel()."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    tool_name: str
    status: Literal["success", "exception"]
    result: Any
    envelope: AgentExceptionEnvelope | None


class FanOutOutcomeEnvelope(BaseModel):
    """Aggregated success/failure view for parallel independent calls."""

    model_config = ConfigDict(frozen=True)

    fan_out_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str | None
    outcomes: list[AgentOutcome]
    aggregate_class: AgentExceptionClass | None
    success_count: int
    failure_count: int
    summary: dict[str, int]


class _EnvelopeCaptureBus:
    def __init__(self, delegate: object) -> None:
        self.envelopes: list[AgentExceptionEnvelope] = []
        self._delegate = delegate

    def publish(self, envelope: AgentExceptionEnvelope) -> None:
        self.envelopes.append(envelope)
        publish = getattr(self._delegate, "publish", None)
        if callable(publish):
            try:
                publish(envelope)
            except Exception:
                return


async def call_parallel(
    calls: list[tuple[ResilienceBundle, str, str, Callable[[], Any]]],
    *,
    correlation_id: str | None = None,
    timeout_seconds: float | None = None,
) -> tuple[list[Any], FanOutOutcomeEnvelope]:
    """Run independent tool/agent calls concurrently and aggregate outcomes.

    Args:
        calls: Tuples of (bundle, tool_name, agent_id, fn).
        correlation_id: Optional trace identifier passed to each wrapped call.
        timeout_seconds: Optional per-call timeout passed through to resilient().
            For synchronous callables, this uses resilient(...,
            allow_sync_llm_timeout=True) internally so parallel orchestration can
            bound wall time. That has the same leaked-worker caveat documented
            on resilient(): the worker thread cannot be forcibly cancelled.

    Returns:
        A tuple of (results, outcome envelope). The results list preserves input
        order and contains None for failed calls.

    Raises:
        AgentHardKillError: Re-raised immediately when any call HARD_KILLs.
    """
    results: list[Any] = [None] * len(calls)
    outcomes: list[AgentOutcome | None] = [None] * len(calls)

    async def _run_one(
        index: int,
        bundle: ResilienceBundle,
        tool_name: str,
        agent_id: str,
        fn: Callable[[], Any],
    ) -> None:
        capture_bus = _EnvelopeCaptureBus(bundle.propagation_bus)
        call_bundle = replace(bundle, propagation_bus=capture_bus)

        def _wrapped_call() -> Any:
            return resilient(
                call_bundle,
                tool_name=tool_name,
                agent_id=agent_id,
                correlation_id=correlation_id,
                fallback_value=_FAILED_CALL,
                timeout_seconds=timeout_seconds,
                allow_sync_llm_timeout=timeout_seconds is not None,
            )(fn)()

        try:
            if inspect.iscoroutinefunction(fn):
                value = await async_resilient(
                    call_bundle,
                    tool_name=tool_name,
                    agent_id=agent_id,
                    correlation_id=correlation_id,
                    fallback_value=_FAILED_CALL,
                    timeout_seconds=timeout_seconds,
                )(fn)()
            else:
                value = await asyncio.to_thread(_wrapped_call)
        except AgentHardKillError:
            raise
        except Exception:
            value = _FAILED_CALL

        if value is _FAILED_CALL:
            envelope = capture_bus.envelopes[-1] if capture_bus.envelopes else None
            outcomes[index] = AgentOutcome(
                agent_id=agent_id,
                tool_name=tool_name,
                status="exception",
                result=None,
                envelope=envelope,
            )
            return

        results[index] = value
        outcomes[index] = AgentOutcome(
            agent_id=agent_id,
            tool_name=tool_name,
            status="success",
            result=value,
            envelope=None,
        )

    try:
        async with asyncio.TaskGroup() as tg:
            for index, (bundle, tool_name, agent_id, fn) in enumerate(calls):
                tg.create_task(_run_one(index, bundle, tool_name, agent_id, fn))
    except* AgentHardKillError as group:
        raise group.exceptions[0] from None

    completed = [outcome for outcome in outcomes if outcome is not None]
    aggregate_class = _aggregate_class(completed)
    summary = _summary(completed)
    success_count = sum(1 for outcome in completed if outcome.status == "success")
    failure_count = len(completed) - success_count

    return results, FanOutOutcomeEnvelope(
        correlation_id=correlation_id,
        outcomes=completed,
        aggregate_class=aggregate_class,
        success_count=success_count,
        failure_count=failure_count,
        summary=summary,
    )


def _aggregate_class(outcomes: list[AgentOutcome]) -> AgentExceptionClass | None:
    classes = [
        outcome.envelope.exception_class
        for outcome in outcomes
        if outcome.envelope is not None
    ]
    if not classes:
        return None
    return max(classes, key=lambda item: _SEVERITY[item])


def _summary(outcomes: list[AgentOutcome]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.envelope is None:
            continue
        key = outcome.envelope.exception_class.value
        summary[key] = summary.get(key, 0) + 1
    return summary
