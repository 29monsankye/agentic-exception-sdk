"""Escalation router — dispatches exception envelopes to tier-appropriate handlers.

Tier separation is structural: EXCEPTION handlers cannot be registered for
ISSUE failures and vice versa. The SDK does not own durable workflow replay.

For L2 checkpoint handoff, handlers return a RecoveryDirective with restored state
and a re-entry hint so the host call site can resume execution. The host owns
actual workflow re-entry.

This is a deliberate SDK boundary: Temporal, LangGraph, or a host step journal
owns durable execution, history, saga-native compensation, and production replay.
When those frameworks are present, use this SDK for taxonomy, trust-boundary
sanitization, AgentHardKillError propagation, and GenAI OTel instrumentation;
let the external framework own replay and compensation.

RecoveryDirective is defined once here. Wrappers import this canonical type
and must not define a second structurally similar class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from agentic_exception_sdk.taxonomy.enums import AgentExceptionClass, EscalationLevel
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "EscalationHandlerLike",
    "EscalationRouter",
    "NoOpEscalationRouter",
    "RecoveryDirective",
]


@dataclass(frozen=True)
class RecoveryDirective:
    """Typed hand-off from an escalation handler back to the resilience wrapper.

    The SDK owns the routing decision. The host owns workflow replay and re-entry.
    When Temporal, LangGraph, or a host step journal is present, handlers should
    translate this directive into the framework's native resume/replay mechanism.

    Attributes:
        action: Recovery action:
            - "resume": return resume_state to the call site (host owns replay).
            - "escalate": fall through to fallback/re-raise.
            - "abort": promote to HARD_KILL / L4_SAFE_ABORT.
        resume_state: Restored state to return to the call site for action="resume".
        resume_from_step: Optional re-entry hint identifying which step to resume at.
    """

    action: Literal["resume", "escalate", "abort"]
    resume_state: Any | None = None
    resume_from_step: str | None = None


@runtime_checkable
class EscalationHandlerLike(Protocol):
    """Protocol for escalation handlers registered in each tier."""

    def handle(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None:
        """Handle an exception envelope and return a recovery directive.

        Args:
            envelope: The exception envelope routed to this handler.

        Returns:
            A RecoveryDirective if the handler has a specific recovery action,
            or None to fall through to default tier behavior.
        """
        ...


class NoOpEscalationRouter:
    """Router that returns None for all envelopes (safe no-op default)."""

    def route(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None:
        """Return None for all envelopes without any routing.

        Args:
            envelope: Ignored.

        Returns:
            None.
        """
        return None


class EscalationRouter:
    """Routes exception envelopes to tier-appropriate escalation handlers.

    Tier separation is structural. EXCEPTION handlers are keyed by EscalationLevel
    in the [L0, L1] range. ISSUE handlers are keyed in the [L2, L3] range. A single
    hard_kill_handler handles all HARD_KILL envelopes.

    Args:
        exception_handlers: Dict mapping EscalationLevel to EscalationHandlerLike
            for the EXCEPTION tier (L0_SELF_RETRY, L1_FALLBACK_PATH). Optional.
        issue_handlers: Dict mapping EscalationLevel to EscalationHandlerLike
            for the ISSUE tier (L2_CHECKPOINT_HANDOFF, L3_HUMAN_ESCALATION). Optional.
        hard_kill_handler: Single handler for the HARD_KILL tier (L4_SAFE_ABORT). Optional.
    """

    def __init__(
        self,
        exception_handlers: dict[EscalationLevel, EscalationHandlerLike] | None = None,
        issue_handlers: dict[EscalationLevel, EscalationHandlerLike] | None = None,
        hard_kill_handler: EscalationHandlerLike | None = None,
    ) -> None:
        self._exception_handlers: dict[EscalationLevel, EscalationHandlerLike] = (
            exception_handlers or {}
        )
        self._issue_handlers: dict[EscalationLevel, EscalationHandlerLike] = (
            issue_handlers or {}
        )
        self._hard_kill_handler: EscalationHandlerLike | None = hard_kill_handler

    def route(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None:
        """Route the envelope to the appropriate tier handler.

        Args:
            envelope: The exception envelope to route.

        Returns:
            RecoveryDirective from the matched handler, or None if no handler matched.
        """
        if envelope.exception_class == AgentExceptionClass.EXCEPTION:
            handler = self._exception_handlers.get(envelope.suggested_recovery)
        elif envelope.exception_class == AgentExceptionClass.ISSUE:
            handler = self._issue_handlers.get(envelope.suggested_recovery)
        else:
            handler = self._hard_kill_handler

        if handler is not None:
            return handler.handle(envelope)
        return None
