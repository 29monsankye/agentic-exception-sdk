"""Orchestrator exception router for multi-agent systems.

OrchestratorExceptionRouter handles routing exception envelopes from an
orchestrator to registered worker agent handlers. fan_out() uses
asyncio.TaskGroup (Python 3.11+) for structured concurrency.

Worker handlers that raise are treated as returning None — fan_out() never
masks the original exception by letting secondary handler failures propagate.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from agentic_exception_sdk.escalation.router import RecoveryDirective
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

_log = logging.getLogger(__name__)

__all__ = [
    "OrchestratorExceptionRouter",
    "WorkerHandler",
    "fan_out",
]

WorkerHandler = Callable[[AgentExceptionEnvelope], Awaitable[RecoveryDirective | None]]


async def fan_out(
    envelope: AgentExceptionEnvelope,
    handlers: list[WorkerHandler],
    *,
    timeout_seconds: float | None = None,
) -> list[RecoveryDirective | None]:
    """Fan out an exception envelope to multiple async worker handlers concurrently.

    Uses asyncio.TaskGroup for structured concurrency (Python 3.11+). Each
    handler runs as an independent task. If a handler raises, its result is
    recorded as None and the exception is logged at DEBUG level. The fan_out
    never re-raises secondary handler exceptions.

    Args:
        envelope: The exception envelope to dispatch.
        handlers: Async callables that accept the envelope and return a directive.
        timeout_seconds: Optional per-fan-out wall-clock timeout using asyncio.timeout().
            When exceeded, all in-flight tasks are cancelled via TaskGroup semantics.

    Returns:
        List of RecoveryDirective | None, one per handler, in input order.
        Handlers that raise return None rather than propagating.
    """
    results: list[RecoveryDirective | None] = [None] * len(handlers)

    async def _run_handler(index: int, handler: WorkerHandler) -> None:
        try:
            results[index] = await handler(envelope)
        except Exception:
            _log.debug(
                "fan_out handler index=%s raised; treating as None directive", index
            )
            results[index] = None

    async def _run_all() -> None:
        async with asyncio.TaskGroup() as tg:
            for i, handler in enumerate(handlers):
                tg.create_task(_run_handler(i, handler))

    if timeout_seconds is not None:
        async with asyncio.timeout(timeout_seconds):
            await _run_all()
    else:
        await _run_all()

    return results


class OrchestratorExceptionRouter:
    """Routes exception envelopes from an orchestrator to registered worker handlers.

    route() fans out to all registered handlers concurrently and returns the
    first non-None RecoveryDirective, or None if all handlers return None.

    Args:
        handlers: Initial list of async worker handlers. May be empty.
        fan_out_timeout_seconds: Optional per-fan-out wall-clock timeout in seconds.
    """

    def __init__(
        self,
        handlers: list[WorkerHandler] | None = None,
        *,
        fan_out_timeout_seconds: float | None = None,
    ) -> None:
        self._handlers: list[WorkerHandler] = list(handlers or [])
        self._fan_out_timeout_seconds = fan_out_timeout_seconds

    def register(self, handler: WorkerHandler) -> None:
        """Register an additional worker handler.

        Args:
            handler: Async callable to add to the routing pool.
        """
        self._handlers.append(handler)

    async def route(
        self,
        envelope: AgentExceptionEnvelope,
    ) -> RecoveryDirective | None:
        """Fan out the envelope to all registered handlers and return first directive.

        Args:
            envelope: The exception envelope to route.

        Returns:
            The first non-None RecoveryDirective from any handler, or None if
            no handlers are registered or all return None.
        """
        if not self._handlers:
            return None

        directives = await fan_out(
            envelope,
            self._handlers,
            timeout_seconds=self._fan_out_timeout_seconds,
        )
        for directive in directives:
            if directive is not None:
                return directive
        return None
