"""In-memory degraded persistence provider."""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field

from agentic_exception_sdk.persistence.provider import (
    Checkpoint,
    PersistedEnvelope,
    ProviderCapabilities,
)
from agentic_exception_sdk.propagation.protocol import envelope_leaf_hash
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = ["NullProvider"]


def _hash_pair(left: str, right: str) -> str:
    return hashlib.sha256(f"{left}{right}".encode("ascii")).hexdigest()


def _merkle_root(leaves: list[str]) -> str:
    # Phase 2 durable/signed providers must not reuse this construction as-is:
    # signed audit roots need domain-separated leaf/internal-node hashing and a
    # non-malleable odd-node strategy before checkpoints become security proofs.
    if not leaves:
        return hashlib.sha256(b"").hexdigest()
    level = leaves
    while len(level) > 1:
        if len(level) % 2 == 1:
            level = [*level, level[-1]]
        level = [_hash_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


@dataclass
class NullProvider:
    """Non-durable in-memory provider for degraded/local operation.

    It stores only a bounded ring of leaf hashes, computes real leaf hashes and
    Merkle roots, and emits unsigned checkpoints. This is intentionally not a
    durable audit backend.
    """

    max_entries: int = 1024
    _leaves: deque[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._leaves = deque(maxlen=self.max_entries)

    def persist(
        self,
        envelope: AgentExceptionEnvelope,
        leaf_hash: str | None = None,
    ) -> PersistedEnvelope:
        """Store an envelope leaf hash in memory and return the persisted record."""
        leaf = leaf_hash if leaf_hash is not None else envelope_leaf_hash(envelope)
        self._leaves.append(leaf)
        return PersistedEnvelope(exception_id=envelope.exception_id, leaf_hash=leaf)

    def checkpoint(self, root: str | None = None, batch_id: str | None = None) -> Checkpoint:
        """Return an unsigned checkpoint for the current in-memory leaf batch."""
        checkpoint_root = root if root is not None else _merkle_root(list(self._leaves))
        checkpoint_batch_id = batch_id if batch_id is not None else "null-provider"
        return Checkpoint(batch_id=checkpoint_batch_id, root=checkpoint_root, signed=False)

    def capabilities(self) -> ProviderCapabilities:
        """Advertise degraded, non-durable capabilities."""
        return ProviderCapabilities(durable=False, checkpoint_signing=False, worm=False)
