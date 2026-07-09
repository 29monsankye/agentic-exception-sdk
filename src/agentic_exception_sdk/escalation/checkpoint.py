"""Checkpoint store protocol and in-memory implementation.

Production CheckpointStore implementations must:
- Document encryption at rest.
- Store only sanitized snapshots or host-approved encrypted payloads.
- Never store raw PII from unsanitized context snapshots.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

__all__ = ["CheckpointStore", "InMemoryCheckpointStore"]


@runtime_checkable
class CheckpointStore(Protocol):
    """Protocol for storing and restoring agent state checkpoints.

    L2 checkpoint handoff handlers call restore() to retrieve host-approved state
    and return it as a RecoveryDirective. The SDK does not own durable workflow
    replay — the host's Temporal/LangGraph/step journal handles re-entry from
    the returned resume_state.

    Production implementations must document encryption at rest and must store
    only sanitized snapshots or host-approved encrypted payloads.
    """

    def save(
        self,
        *,
        agent_id: str,
        correlation_id: str | None,
        state: Any,
    ) -> None:
        """Save agent state checkpoint.

        Args:
            agent_id: Identifier of the agent saving the checkpoint.
            correlation_id: End-to-end trace identifier.
            state: Serializable state snapshot to persist.
        """
        ...

    def restore(
        self,
        *,
        agent_id: str,
        correlation_id: str | None,
    ) -> Any:
        """Restore the most recent checkpoint for this agent/correlation pair.

        Args:
            agent_id: Identifier of the agent to restore.
            correlation_id: End-to-end trace identifier.

        Returns:
            The most recently saved state, or None if no checkpoint exists.
        """
        ...


class InMemoryCheckpointStore:
    """In-memory checkpoint store for development and testing.

    Thread-safe using threading.RLock. Not suitable for production without a
    durable, encrypted backing store.

    Production implementations must document encryption at rest and must not
    store unsanitized PII.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str | None], Any] = {}
        self._lock = threading.RLock()

    def save(
        self,
        *,
        agent_id: str,
        correlation_id: str | None,
        state: Any,
    ) -> None:
        """Save agent state checkpoint.

        Args:
            agent_id: Identifier of the agent saving the checkpoint.
            correlation_id: End-to-end trace identifier.
            state: State to persist (should be sanitized before saving).
        """
        with self._lock:
            self._store[(agent_id, correlation_id)] = state

    def restore(
        self,
        *,
        agent_id: str,
        correlation_id: str | None,
    ) -> Any:
        """Restore the most recent checkpoint for this agent/correlation pair.

        Args:
            agent_id: Identifier of the agent to restore.
            correlation_id: End-to-end trace identifier.

        Returns:
            The saved state, or None if no checkpoint exists.
        """
        with self._lock:
            return self._store.get((agent_id, correlation_id))

    def clear(self) -> None:
        """Clear all stored checkpoints. Used for testing."""
        with self._lock:
            self._store.clear()
