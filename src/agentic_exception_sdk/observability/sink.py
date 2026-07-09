"""Exception event sink protocol and NoOp implementation.

Sinks are the primary observability boundary. OTelExceptionAdapter and
StructuredLogEmitter both implement ExceptionEventSink. The host injects
its own sink — the SDK never creates a default networked sink.

force_flush() is called by force_flush_exception_telemetry() before
AgentHardKillError is raised so that BatchSpanProcessor-backed sinks
do not lose HARD_KILL spans on process exit.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "ExceptionEventSink",
    "NoOpSink",
]


@runtime_checkable
class ExceptionEventSink(Protocol):
    """Observability sink for exception envelopes.

    Implementations must:
    - Never raise from emit() — all errors must be caught internally.
    - Call force_flush() before returning in hard-kill scenarios.
    - Not emit raw PII from unsanitized envelopes.
    """

    def emit(self, envelope: AgentExceptionEnvelope) -> None:
        """Emit an exception envelope to the sink.

        Args:
            envelope: The sanitized, classified exception envelope.
        """
        ...

    def force_flush(self) -> None:
        """Flush buffered spans or events before a HARD_KILL termination.

        Called by force_flush_exception_telemetry() before AgentHardKillError
        is raised. BatchSpanProcessor-backed implementations must flush here.
        """
        ...


class NoOpSink:
    """Sink that silently discards all envelopes.

    Use as the default sink in ResilienceBundle when no observability backend
    is configured. Safe for development and testing.
    """

    def emit(self, envelope: AgentExceptionEnvelope) -> None:
        """Discard the envelope without any action.

        Args:
            envelope: Ignored.
        """
        return None

    def force_flush(self) -> None:
        """No-op flush."""
        return None
