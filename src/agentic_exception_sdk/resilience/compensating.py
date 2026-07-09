"""Compensating transaction registry for LIFO rollback of side-effecting steps.

CompensationPartialFailureError is always classified as HARD_KILL / L4_SAFE_ABORT
because a partially failed rollback leaves the system in an inconsistent state
that cannot be automatically recovered safely.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from agentic_exception_sdk.taxonomy.errors import CompensationPartialFailureError

_log = logging.getLogger(__name__)

__all__ = ["CompensatingTransactionRegistry"]


class CompensatingTransactionRegistry:
    """Thread-safe registry of compensating (undo) transactions for side-effecting steps.

    Compensators are registered per correlation_id and executed in LIFO order
    when an ISSUE rollback or HARD_KILL abort requires cleanup. If any compensators
    fail, the registry attempts all remaining compensators before raising
    CompensationPartialFailureError with the list of failed step_ids.

    Compensation handlers must be idempotent. The SDK invokes them only through
    explicit rollback paths; it does not infer domain-specific undo logic.
    """

    def __init__(self) -> None:
        # keyed by correlation_id, value is list of (step_id, compensate_fn) in order
        self._registry: dict[str, list[tuple[str, Callable[[], None]]]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        *,
        correlation_id: str,
        step_id: str,
        compensate: Callable[[], None],
    ) -> None:
        """Register a compensating transaction for a step.

        Args:
            correlation_id: End-to-end trace ID grouping steps in one agent run.
            step_id: Unique identifier for this step within the run.
            compensate: Zero-argument callable that undoes the step's side effects.
                        Must be idempotent.
        """
        with self._lock:
            if correlation_id not in self._registry:
                self._registry[correlation_id] = []
            self._registry[correlation_id].append((step_id, compensate))

    def compensate(self, correlation_id: str) -> None:
        """Execute all registered compensators for the correlation_id in LIFO order.

        If one or more compensators fail, all remaining compensators are still
        attempted. After all attempts, CompensationPartialFailureError is raised
        with the list of failed step_ids.

        Args:
            correlation_id: The run identifier whose steps should be rolled back.

        Raises:
            CompensationPartialFailureError: If any compensator raised an exception.
        """
        with self._lock:
            steps = list(self._registry.get(correlation_id, []))

        failed_step_ids: list[str] = []
        for step_id, compensate_fn in reversed(steps):
            try:
                compensate_fn()
                _log.debug("compensated step %s for correlation %s", step_id, correlation_id)
            except Exception:
                failed_step_ids.append(step_id)
                _log.debug(
                    "compensator failed for step %s correlation %s",
                    step_id,
                    correlation_id,
                )

        if failed_step_ids:
            raise CompensationPartialFailureError(failed_step_ids)

    def clear(self, correlation_id: str) -> None:
        """Remove all registered compensators for the given correlation_id.

        Args:
            correlation_id: The run identifier to clear.
        """
        with self._lock:
            self._registry.pop(correlation_id, None)
