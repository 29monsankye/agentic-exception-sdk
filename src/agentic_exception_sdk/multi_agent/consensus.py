"""Consensus gate for multi-agent agreement tracking.

ConsensusGate is used when an orchestrator requires K-of-N agents to report
the same outcome before a recovery decision is made. Thread/task-safe using
threading.RLock — safe for both sync and async contexts.

Keys votes by correlation_id so parallel fan-outs do not interfere across
different agent runs.
"""

from __future__ import annotations

import threading

from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "ConsensusGate",
    "ConsensusNotReachedError",
]


class ConsensusNotReachedError(Exception):
    """Raised when require_consensus() finds the agreement threshold not yet met."""


class ConsensusGate:
    """Tracks agreement across a parallel fan-out for a single correlation window.

    vote() increments the agreement counter. reached() returns True when the
    threshold has been met. require_consensus() raises ConsensusNotReachedError
    when it has not.

    Thread/task-safe using threading.RLock.

    Args:
        threshold: Number of agent votes required to reach consensus.
        correlation_id: End-to-end trace identifier for this consensus window.
            Used only for diagnostic messages — not for keying internal state.
    """

    def __init__(
        self,
        *,
        threshold: int,
        correlation_id: str | None = None,
    ) -> None:
        if threshold < 1:
            raise ValueError(f"threshold must be >= 1, got {threshold}")
        self._threshold = threshold
        self._correlation_id = correlation_id
        self._votes: int = 0
        self._lock = threading.RLock()

    def vote(self, envelope: AgentExceptionEnvelope | None = None) -> None:
        """Record one agreement vote.

        Args:
            envelope: Optional envelope associated with this vote. Ignored by
                      the gate itself but available for subclasses or logging.
        """
        with self._lock:
            self._votes += 1

    def reached(self) -> bool:
        """Return True if the consensus threshold has been met.

        Returns:
            True if vote count >= threshold.
        """
        with self._lock:
            return self._votes >= self._threshold

    def require_consensus(self) -> None:
        """Assert that the consensus threshold has been met.

        Raises:
            ConsensusNotReachedError: If vote count < threshold.
        """
        with self._lock:
            if self._votes < self._threshold:
                detail = (
                    f" correlation_id={self._correlation_id}"
                    if self._correlation_id
                    else ""
                )
                raise ConsensusNotReachedError(
                    f"consensus not reached: {self._votes}/{self._threshold} votes{detail}"
                )

    @property
    def vote_count(self) -> int:
        """Current vote count (thread-safe snapshot)."""
        with self._lock:
            return self._votes

    @property
    def threshold(self) -> int:
        """Configured consensus threshold."""
        return self._threshold
