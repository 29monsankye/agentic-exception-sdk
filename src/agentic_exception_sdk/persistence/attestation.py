"""Runtime attestation for the active persistence provider."""

from __future__ import annotations

from agentic_exception_sdk.persistence.null_provider import NullProvider
from agentic_exception_sdk.persistence.provider import PersistenceProvider, ProviderCapabilities

__all__ = [
    "attestation",
    "get_active_provider",
    "get_provider_capabilities",
    "set_active_provider",
]

_active_provider: PersistenceProvider = NullProvider()


def set_active_provider(provider: PersistenceProvider) -> None:
    """Set the process-local provider used for attestation and degraded markers."""
    global _active_provider
    _active_provider = provider


def get_active_provider() -> PersistenceProvider:
    """Return the process-local active persistence provider."""
    return _active_provider


def get_provider_capabilities() -> ProviderCapabilities:
    """Return capabilities for the active provider."""
    return _active_provider.capabilities()


def attestation() -> dict[str, bool]:
    """Return active provider capabilities as a dashboard-friendly dict."""
    return get_provider_capabilities().as_dict()
