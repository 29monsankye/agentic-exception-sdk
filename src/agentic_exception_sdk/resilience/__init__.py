"""Resilience: retry, circuit breaker, fallback, compensating transactions, and wrappers."""

from __future__ import annotations

from agentic_exception_sdk.resilience.circuit_breaker import (
    AsyncCircuitBreaker,
    AsyncInMemoryCircuitBreaker,
    CircuitBreaker,
    CircuitState,
    InMemoryCircuitBreaker,
    NoOpAsyncCircuitBreaker,
    NoOpCircuitBreaker,
    RedisCircuitBreaker,
)
from agentic_exception_sdk.resilience.compensating import CompensatingTransactionRegistry
from agentic_exception_sdk.resilience.fallback import (
    FallbackChain,
    NoOpFallback,
    OrderedFallbackChain,
)
from agentic_exception_sdk.resilience.retry import (
    ExponentialBackoffRetry,
    NoOpRetry,
    RetryContext,
    RetryPolicy,
)
from agentic_exception_sdk.resilience.wrap import (
    MISSING,
    ResilientCallConfig,
    async_resilient,
    extend_lineage,
    force_flush_exception_telemetry,
    resilient,
)

__all__ = [
    "MISSING",
    "AsyncCircuitBreaker",
    "AsyncInMemoryCircuitBreaker",
    "CircuitBreaker",
    "CircuitState",
    "CompensatingTransactionRegistry",
    "ExponentialBackoffRetry",
    "FallbackChain",
    "InMemoryCircuitBreaker",
    "NoOpAsyncCircuitBreaker",
    "NoOpCircuitBreaker",
    "NoOpFallback",
    "NoOpRetry",
    "OrderedFallbackChain",
    "RedisCircuitBreaker",
    "ResilientCallConfig",
    "RetryContext",
    "RetryPolicy",
    "async_resilient",
    "extend_lineage",
    "force_flush_exception_telemetry",
    "resilient",
]
