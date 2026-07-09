"""Persistence provider boundary and default in-memory implementation."""

from agentic_exception_sdk.persistence.attestation import (
    attestation,
    get_active_provider,
    set_active_provider,
)
from agentic_exception_sdk.persistence.availability import AvailabilityMode
from agentic_exception_sdk.persistence.null_provider import NullProvider
from agentic_exception_sdk.persistence.provider import (
    Checkpoint,
    PersistedEnvelope,
    PersistenceProvider,
    ProviderCapabilities,
)

__all__ = [
    "AvailabilityMode",
    "Checkpoint",
    "NullProvider",
    "PersistedEnvelope",
    "PersistenceProvider",
    "ProviderCapabilities",
    "attestation",
    "get_active_provider",
    "set_active_provider",
]
