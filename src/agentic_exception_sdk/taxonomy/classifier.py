"""Default exception classifier — maps caught exceptions to taxonomy routing classes.

The default rule for unknown exception types is HARD_KILL. An unrecognised exception
is never assumed to be safely retryable.

asyncio.CancelledError, KeyboardInterrupt, and SystemExit are control-flow signals,
not agent failures. They are re-raised unchanged before any classification, envelope
construction, or emission — both as top-level exceptions and when encountered inside
a BaseExceptionGroup.
"""

from __future__ import annotations

import asyncio

from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.errors import (
    BudgetExhaustedError,
    BudgetWarningError,
    CircuitBreakerStateUnavailableError,
    CompensationPartialFailureError,
    FallbackCapableError,
    GuardRailViolationError,
    PromptInjectionError,
    SecurityViolationError,
    SLAViolationError,
    StateCorruptionError,
    ValidationGateError,
)

__all__ = ["ExceptionClassifier"]

_SEVERITY: dict[AgentExceptionClass, int] = {
    AgentExceptionClass.EXCEPTION: 0,
    AgentExceptionClass.ISSUE: 1,
    AgentExceptionClass.HARD_KILL: 2,
}

_Classification = tuple[AgentExceptionClass, ExceptionSource, EscalationLevel]


class ExceptionClassifier:
    """Maps a caught exception to (AgentExceptionClass, ExceptionSource, EscalationLevel).

    Host projects may inject a custom classifier via ResilienceBundle to override
    the default classification logic. Custom classifiers must honour the same
    control-flow pass-through contract for CancelledError, KeyboardInterrupt,
    and SystemExit.
    """

    def classify(self, exc: BaseException) -> _Classification:
        """Classify an exception into the three-tier routing model.

        Control-flow signals (CancelledError, KeyboardInterrupt, SystemExit) are
        re-raised unchanged rather than classified.

        BaseExceptionGroup instances are recursively unwrapped. Mixed groups
        containing control-flow signals are split: the non-control branch is
        classified independently and the control branch is re-raised after routing.

        Args:
            exc: The caught exception to classify.

        Returns:
            A tuple of (AgentExceptionClass, ExceptionSource, EscalationLevel).

        Raises:
            asyncio.CancelledError: When exc is a cancellation signal.
            KeyboardInterrupt: When exc is a keyboard interrupt.
            SystemExit: When exc is a system exit.
            RuntimeError: When a control-flow-only BaseExceptionGroup is received
                          (split must happen before classification).
        """
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise exc

        if isinstance(exc, BaseExceptionGroup):
            return self._classify_exception_group(exc)

        return self._classify_single(exc)

    def _classify_single(self, exc: BaseException) -> _Classification:
        """Classify a single non-group exception.

        Args:
            exc: A single exception (not a BaseExceptionGroup).

        Returns:
            A tuple of (AgentExceptionClass, ExceptionSource, EscalationLevel).
        """
        # HARD_KILL — security and irrecoverable failures
        if isinstance(exc, (
            PromptInjectionError,
            SecurityViolationError,
            StateCorruptionError,
            CompensationPartialFailureError,
            GuardRailViolationError,
        )):
            return (
                AgentExceptionClass.HARD_KILL,
                ExceptionSource.ORCHESTRATION,
                EscalationLevel.L4_SAFE_ABORT,
            )

        if isinstance(exc, BudgetExhaustedError):
            return (
                AgentExceptionClass.HARD_KILL,
                ExceptionSource.ORCHESTRATION,
                EscalationLevel.L4_SAFE_ABORT,
            )

        # ISSUE — requires human or external intervention
        if isinstance(exc, BudgetWarningError):
            return (
                AgentExceptionClass.ISSUE,
                ExceptionSource.ORCHESTRATION,
                EscalationLevel.L3_HUMAN_ESCALATION,
            )

        if isinstance(exc, SLAViolationError):
            return (
                AgentExceptionClass.ISSUE,
                ExceptionSource.ORCHESTRATION,
                EscalationLevel.L3_HUMAN_ESCALATION,
            )

        if isinstance(exc, (PermissionError, FileNotFoundError)):
            return (
                AgentExceptionClass.ISSUE,
                ExceptionSource.DATA_ENV,
                EscalationLevel.L2_CHECKPOINT_HANDOFF,
            )

        # EXCEPTION — automatic recovery possible
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return (
                AgentExceptionClass.EXCEPTION,
                ExceptionSource.TOOL,
                EscalationLevel.L0_SELF_RETRY,
            )

        if isinstance(exc, CircuitBreakerStateUnavailableError):
            return (
                AgentExceptionClass.EXCEPTION,
                ExceptionSource.TOOL,
                EscalationLevel.L0_SELF_RETRY,
            )

        if isinstance(exc, ValidationGateError):
            return (
                AgentExceptionClass.ISSUE,
                ExceptionSource.MODEL,
                EscalationLevel.L3_HUMAN_ESCALATION,
            )

        if isinstance(exc, (ValueError, TypeError)) and self._has_fallback(exc):
            return (
                AgentExceptionClass.EXCEPTION,
                ExceptionSource.MODEL,
                EscalationLevel.L1_FALLBACK_PATH,
            )

        # Unknown exception type — default to HARD_KILL (safe: never hide unknown failures)
        return (
            AgentExceptionClass.HARD_KILL,
            ExceptionSource.ORCHESTRATION,
            EscalationLevel.L4_SAFE_ABORT,
        )

    def _has_fallback(self, exc: BaseException) -> bool:
        """Return True only when the exception is a FallbackCapableError subclass.

        Duck-typing (getattr checks) is explicitly not supported.
        """
        return isinstance(exc, FallbackCapableError)

    def _classify_exception_group(
        self,
        exc: BaseExceptionGroup[BaseException],
    ) -> _Classification:
        """Classify a BaseExceptionGroup by finding the most severe nested classification.

        Control-flow signals nested inside the group are split out and re-raised
        so they are not silently dropped alongside sibling security failures.

        Args:
            exc: A BaseExceptionGroup to classify.

        Returns:
            The most severe classification among all non-control failures.

        Raises:
            RuntimeError: If the group contains only control-flow exceptions (the
                          caller must split and re-raise those before invoking the
                          classifier).
        """
        control_types = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
        control, failures = exc.split(control_types)

        if control is not None:
            # Control-flow branches must be split and re-raised by resilient()
            # before classification. Reaching here means the caller did not split.
            raise RuntimeError(
                "control-flow BaseExceptionGroup branch must be split and "
                "re-raised by resilient() before classification"
            )

        if failures is None:
            raise RuntimeError("non-control exception group is empty after split")

        classifications = [self.classify(child) for child in failures.exceptions]
        return max(classifications, key=lambda item: _SEVERITY[item[0]])
