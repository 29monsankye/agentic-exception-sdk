from agentic_exception_sdk.persistence.provider import (
    Checkpoint,
    PersistedEnvelope,
    ProviderCapabilities,
)
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = ['NullProvider']

class NullProvider:
    max_entries: int
    def __init__(self, max_entries: int = 1024) -> None: ...
    def persist(
        self,
        envelope: AgentExceptionEnvelope,
        leaf_hash: str | None = None,
    ) -> PersistedEnvelope: ...
    def checkpoint(self, root: str | None = None, batch_id: str | None = None) -> Checkpoint: ...
    def capabilities(self) -> ProviderCapabilities: ...
