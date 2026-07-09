"""Built-in escalation handler implementations."""

from __future__ import annotations

from agentic_exception_sdk.escalation.router import RecoveryDirective
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = ["NoOpHandler"]


class NoOpHandler:
    """Handler that returns None — no recovery action taken.

    Use as a placeholder for tiers where the retry_policy or fallback_chain
    already handles recovery without requiring explicit handler logic.
    """

    def handle(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None:
        """Return None without any action.

        Args:
            envelope: Ignored.

        Returns:
            None.
        """
        return None
