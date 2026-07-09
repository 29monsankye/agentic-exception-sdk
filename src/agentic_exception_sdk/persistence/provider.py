"""PersistenceProvider protocol for audit-envelope storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "Checkpoint",
    "PersistedEnvelope",
    "PersistenceProvider",
    "ProviderCapabilities",
]


@dataclass(frozen=True)
class ProviderCapabilities:
    """Security and durability properties advertised by a persistence provider."""

    durable: bool
    checkpoint_signing: bool
    worm: bool

    def as_dict(self) -> dict[str, bool]:
        """Return JSON/dashboard-friendly capability flags."""
        return {
            "durable": self.durable,
            "checkpoint_signing": self.checkpoint_signing,
            "worm": self.worm,
        }


@dataclass(frozen=True)
class PersistedEnvelope:
    """Result returned when a provider persists an envelope leaf."""

    exception_id: str
    leaf_hash: str


@dataclass(frozen=True)
class Checkpoint:
    """Merkle checkpoint for a batch of persisted envelope leaves."""

    batch_id: str
    root: str
    signed: bool


@runtime_checkable
class PersistenceProvider(Protocol):
    """Abstract boundary implemented by SDK and external audit providers."""

    def persist(
        self,
        envelope: AgentExceptionEnvelope,
        leaf_hash: str | None = None,
    ) -> PersistedEnvelope:
        """Persist an envelope and its integrity leaf hash."""
        ...

    def checkpoint(self, root: str | None = None, batch_id: str | None = None) -> Checkpoint:
        """Emit a checkpoint for the current batch/root."""
        ...

    def capabilities(self) -> ProviderCapabilities:
        """Return provider security and durability capabilities."""
        ...
