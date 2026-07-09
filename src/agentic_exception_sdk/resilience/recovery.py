"""Post-classification recovery policy hooks."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentic_exception_sdk.escalation.router import RecoveryDirective
from agentic_exception_sdk.taxonomy.enums import EscalationLevel
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "RecoveryPolicy",
    "SuggestedRecoveryPolicy",
]


@runtime_checkable
class RecoveryPolicy(Protocol):
    """Protocol for post-classification recovery decisions."""

    def recover(
        self,
        envelope: AgentExceptionEnvelope,
    ) -> RecoveryDirective | None:
        """Return a directive to override default recovery, or None to fall through.

        Args:
            envelope: Already-classified and sanitized exception envelope.

        Returns:
            A RecoveryDirective to apply, or None for SDK defaults.
        """
        ...


class SuggestedRecoveryPolicy:
    """Routes based on envelope.suggested_recovery level."""

    def recover(
        self,
        envelope: AgentExceptionEnvelope,
    ) -> RecoveryDirective | None:
        """Return a directive based on the envelope's suggested recovery level."""
        if envelope.suggested_recovery == EscalationLevel.L0_SELF_RETRY:
            return None
        if envelope.suggested_recovery == EscalationLevel.L1_FALLBACK_PATH:
            return RecoveryDirective(action="resume", resume_state=None)
        if envelope.suggested_recovery >= EscalationLevel.L2_CHECKPOINT_HANDOFF:
            return RecoveryDirective(action="escalate")
        return None
