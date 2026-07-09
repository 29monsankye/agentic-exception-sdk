"""Factory for a ResilienceBundle with all no-op / safe-default components.

NoOpResilienceBundle() is the canonical starting point for tests and for host
projects that want to configure only specific components while letting the
rest default safely.
"""

from __future__ import annotations

from agentic_exception_sdk.bundle import ResilienceBundle

__all__ = ["NoOpResilienceBundle"]


def NoOpResilienceBundle() -> ResilienceBundle:
    """Return a ResilienceBundle with all no-op / safe-default components.

    Recommended starting point for tests and for wiring up individual components:

        bundle = NoOpResilienceBundle()
        bundle.circuit_breaker = InMemoryCircuitBreaker(failure_threshold=3)
        bundle.exception_sink = StructuredLogEmitter()

    Returns:
        A ResilienceBundle with all components set to their safe defaults.
    """
    return ResilienceBundle()
