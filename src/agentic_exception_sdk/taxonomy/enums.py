"""Primary taxonomy enums that drive exception routing."""

from __future__ import annotations

from enum import Enum, StrEnum

__all__ = ["AgentExceptionClass", "EscalationLevel", "ExceptionSource"]


class AgentExceptionClass(StrEnum):
    """Primary routing class. Determines which handler tier processes the failure.

    Every failure is assigned exactly one class; the class determines the routing
    path and nothing else does.
    """

    EXCEPTION = "exception"
    """Gracefully resolvable. Automatic recovery is possible without human input."""

    ISSUE = "issue"
    """Non-gracefully resolvable. Recovery requires external intervention.
    The agent pauses; it does not terminate.
    """

    HARD_KILL = "hard_kill"
    """Not resolvable. The agent must terminate immediately.
    Full state is logged and the envelope goes to the DLQ.
    """


class ExceptionSource(StrEnum):
    """Informational. Records where the failure originated.

    Does not drive routing; used for diagnostics and observability only.
    """

    MODEL = "model"
    """Hallucination, context overflow, injection, or toxic output from the LLM."""

    TOOL = "tool"
    """API timeout, schema mismatch, auth error, or partial tool execution."""

    ORCHESTRATION = "orchestration"
    """Infinite loop, deadlock, state inconsistency, or bad agent handoff."""

    PLANNING = "planning"
    """Planning-loop failure detected by transcript observation.
    Covers repeated tool calls, no-progress turns, and planning budget exhaustion.
    """

    DATA_ENV = "data_env"
    """Stale context, file not found, write conflict, or environment drift."""


class EscalationLevel(int, Enum):
    """Specific recovery action within an AgentExceptionClass.

    The mapping between class and level is fixed and enforced by the SDK:
    - EXCEPTION  -> L0_SELF_RETRY, L1_FALLBACK_PATH
    - ISSUE      -> L2_CHECKPOINT_HANDOFF, L3_HUMAN_ESCALATION
    - HARD_KILL  -> L4_SAFE_ABORT (always)
    """

    L0_SELF_RETRY = 0
    """Retry with exponential backoff and jitter. No human involved."""

    L1_FALLBACK_PATH = 1
    """Switch to alternate tool, cached result, or reduced-scope response."""

    L2_CHECKPOINT_HANDOFF = 2
    """Hand restored state and re-entry hint back to host. The host owns replay."""

    L3_HUMAN_ESCALATION = 3
    """Pause execution. Notify operator. Await explicit decision before continuing."""

    L4_SAFE_ABORT = 4
    """Terminate agent immediately. Log full state. Write envelope to DLQ."""
