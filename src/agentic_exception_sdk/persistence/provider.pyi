from dataclasses import dataclass
from typing import Protocol

from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = ['Checkpoint', 'PersistedEnvelope', 'PersistenceProvider', 'ProviderCapabilities']

@dataclass(frozen=True)
class ProviderCapabilities:
    durable: bool
    checkpoint_signing: bool
    worm: bool
    def as_dict(self) -> dict[str, bool]: ...

@dataclass(frozen=True)
class PersistedEnvelope:
    exception_id: str
    leaf_hash: str

@dataclass(frozen=True)
class Checkpoint:
    batch_id: str
    root: str
    signed: bool

class PersistenceProvider(Protocol):
    def persist(
        self,
        envelope: AgentExceptionEnvelope,
        leaf_hash: str | None = None,
    ) -> PersistedEnvelope: ...
    def checkpoint(self, root: str | None = None, batch_id: str | None = None) -> Checkpoint: ...
    def capabilities(self) -> ProviderCapabilities: ...
