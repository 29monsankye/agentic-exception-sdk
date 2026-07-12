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
        correlation_id: Default end-to-end trace identifier for votes and reads.
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
        self._votes: dict[str, int] = {}
        self._lock = threading.RLock()

    def vote(self, envelope: AgentExceptionEnvelope | None = None) -> None:
        """Record one agreement vote.

        Args:
            envelope: Optional envelope associated with this vote. Ignored by
                      the gate itself but available for subclasses or logging.
        """
        correlation_id = (
            (envelope.correlation_id if envelope is not None else None)
            or self._correlation_id
            or "global"
        )
        with self._lock:
            self._votes[correlation_id] = self._votes.get(correlation_id, 0) + 1

    def reached(self, correlation_id: str | None = None) -> bool:
        """Return True if the consensus threshold has been met.

        Returns:
            True if vote count >= threshold.
        """
        key = correlation_id or self._correlation_id or "global"
        with self._lock:
            return self._votes.get(key, 0) >= self._threshold

    def require_consensus(self, correlation_id: str | None = None) -> None:
        """Assert that the consensus threshold has been met.

        Raises:
            ConsensusNotReachedError: If vote count < threshold.
        """
        key = correlation_id or self._correlation_id or "global"
        with self._lock:
            votes = self._votes.get(key, 0)
            if votes < self._threshold:
                raise ConsensusNotReachedError(
                    f"consensus not reached: {votes}/{self._threshold} votes "
                    f"for correlation_id={key}"
                )

    def votes_for(self, correlation_id: str | None = None) -> int:
        """Return the vote count for a specific correlation window.

        Args:
            correlation_id: Window to read; defaults to the gate's configured
                correlation_id (or the shared "global" window).
        """
        key = correlation_id or self._correlation_id or "global"
        with self._lock:
            return self._votes.get(key, 0)

    def reset(self, correlation_id: str | None = None) -> None:
        """Drop stored votes for one correlation window.

        Callers must invoke this (or clear()) once a correlation window is
        resolved; otherwise per-correlation vote counters accumulate for the
        lifetime of the gate and leak memory on long-lived orchestrators.

        Args:
            correlation_id: Window to drop; defaults to the gate's configured
                correlation_id (or the shared "global" window).
        """
        key = correlation_id or self._correlation_id or "global"
        with self._lock:
            self._votes.pop(key, None)

    def clear(self) -> None:
        """Drop every stored vote counter across all correlation windows."""
        with self._lock:
            self._votes.clear()

    @property
    def vote_count(self) -> int:
        """Vote count for the gate's default correlation window (snapshot).

        Reads the gate's configured correlation_id window (or "global"). Use
        votes_for(correlation_id) to read envelope-keyed windows.
        """
        key = self._correlation_id or "global"
        with self._lock:
            return self._votes.get(key, 0)

    @property
    def threshold(self) -> int:
        """Configured consensus threshold."""
        return self._threshold
