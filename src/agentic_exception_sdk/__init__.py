"""Agentic Exception Management SDK.

A protocol-first, zero-host-import SDK for structured exception handling,
classification, retry, circuit breaking, propagation, and observability in
agentic AI systems.

Quick start:

    from agentic_exception_sdk import (
        NoOpResilienceBundle,
        ResilienceBundle,
        async_resilient,
        resilient,
    )

    bundle = NoOpResilienceBundle()

    @resilient(bundle, tool_name="search_flights", agent_id="travel-agent")
    def search_flights(query: str) -> list[dict]:
        ...
"""

from __future__ import annotations

from importlib.metadata import version as _metadata_version

__version__ = _metadata_version("agentic-exception-sdk")

# Budget
from agentic_exception_sdk.budget.models import AgentBudget, UnlimitedBudget
from agentic_exception_sdk.budget.watchdog import BudgetWatchdog

# Bundle and factory
from agentic_exception_sdk.bundle import ResilienceBundle
from agentic_exception_sdk.config.validate import BundleValidationError, validate_bundle

# Escalation
from agentic_exception_sdk.escalation.checkpoint import (
    CheckpointStore,
    InMemoryCheckpointStore,
)
from agentic_exception_sdk.escalation.handlers import NoOpHandler
from agentic_exception_sdk.escalation.router import (
    EscalationHandlerLike,
    EscalationRouter,
    NoOpEscalationRouter,
    RecoveryDirective,
)

# Multi-agent
from agentic_exception_sdk.multi_agent.consensus import (
    ConsensusGate,
    ConsensusNotReachedError,
)
from agentic_exception_sdk.multi_agent.parallel import (
    AgentOutcome,
    FanOutOutcomeEnvelope,
    call_parallel,
)
from agentic_exception_sdk.multi_agent.router import (
    OrchestratorExceptionRouter,
    WorkerHandler,
    fan_out,
)
from agentic_exception_sdk.multi_agent.sla import AgentSLAPolicy
from agentic_exception_sdk.noop import NoOpResilienceBundle
from agentic_exception_sdk.observability.metrics import (
    InMemoryMetricsCollector,
    MetricsCollector,
    NoOpMetricsCollector,
    PrometheusMetricsCollector,
)
from agentic_exception_sdk.observability.otel import OTelExceptionAdapter

# Observability
from agentic_exception_sdk.observability.sink import ExceptionEventSink, NoOpSink
from agentic_exception_sdk.observability.structured_log import StructuredLogEmitter
from agentic_exception_sdk.persistence import (
    AvailabilityMode,
    Checkpoint,
    NullProvider,
    PersistedEnvelope,
    PersistenceProvider,
    ProviderCapabilities,
    attestation,
    get_active_provider,
    set_active_provider,
)

# Propagation
from agentic_exception_sdk.propagation.bus import (
    AsyncExceptionPropagationBus,
    AsyncInMemoryBus,
    ExceptionPropagationBus,
    InMemoryBus,
    PropagationBufferFullError,
)
from agentic_exception_sdk.propagation.dlq import (
    AsyncInMemoryDLQ,
    DeadLetterQueue,
    InMemoryDLQ,
)
from agentic_exception_sdk.propagation.protocol import (
    envelope_canonical_bytes,
    envelope_canonical_sha256,
    envelope_debug_repr,
    envelope_from_json,
    envelope_leaf_hash,
    envelope_to_json,
)

# Resilience components
from agentic_exception_sdk.resilience.circuit_breaker import (
    AsyncInMemoryCircuitBreaker,
    CircuitState,
    InMemoryCircuitBreaker,
    NoOpAsyncCircuitBreaker,
    NoOpCircuitBreaker,
)
from agentic_exception_sdk.resilience.compensating import (
    CompensatingTransactionRegistry,
)
from agentic_exception_sdk.resilience.fallback import (
    FallbackChain,
    NoOpFallback,
    OrderedFallbackChain,
)
from agentic_exception_sdk.resilience.retry import (
    ExponentialBackoffRetry,
    InMemoryRetryInFlightTracker,
    NoOpRetry,
    RetryContext,
    RetryInFlightEntry,
    RetryInFlightTracker,
)

# Primary resilience entry points
from agentic_exception_sdk.resilience.wrap import (
    MISSING,
    ResilientCallConfig,
    async_resilient,
    extend_lineage,
    force_flush_exception_telemetry,
    resilient,
)
from agentic_exception_sdk.taxonomy.classifier import ExceptionClassifier

# Core taxonomy — enums, errors, envelope, classifier
from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.envelope import (
    AgentExceptionEnvelope,
    SafeContextSnapshot,
    SafeContextValue,
)
from agentic_exception_sdk.taxonomy.errors import (
    AgentHardKillError,
    BudgetExhaustedError,
    BudgetWarningError,
    CircuitBreakerStateUnavailableError,
    CompensationPartialFailureError,
    FallbackCapableError,
    GuardRailViolationError,
    PromptInjectionError,
    SecurityViolationError,
    SLAViolationError,
    StateCorruptionError,
    ToolKindMismatchError,
    ValidationGateError,
)

# Validation
from agentic_exception_sdk.validation.gates import (
    NoOpGate,
    OutputValidationGate,
    PydanticValidationGate,
)
from agentic_exception_sdk.validation.guard_rails import (
    AllowlistedOperations,
    GuardRailPolicy,
    NoOpGuardRails,
)
from agentic_exception_sdk.validation.rules_version import RULES_VERSION
from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator

__all__ = [
    "__version__",
    # Taxonomy
    "AgentExceptionClass",
    "AgentExceptionEnvelope",
    "AgentHardKillError",
    "BudgetExhaustedError",
    "BudgetWarningError",
    "CircuitBreakerStateUnavailableError",
    "CompensationPartialFailureError",
    "EscalationLevel",
    "ExceptionClassifier",
    "ExceptionSource",
    "FallbackCapableError",
    "GuardRailViolationError",
    "PromptInjectionError",
    "SafeContextSnapshot",
    "SafeContextValue",
    "SecurityViolationError",
    "SLAViolationError",
    "StateCorruptionError",
    "ToolKindMismatchError",
    "ValidationGateError",
    # Bundle
    "BundleValidationError",
    "NoOpResilienceBundle",
    "ResilienceBundle",
    "validate_bundle",
    # Resilience wrappers
    "MISSING",
    "ResilientCallConfig",
    "async_resilient",
    "extend_lineage",
    "force_flush_exception_telemetry",
    "resilient",
    # Circuit breakers
    "AsyncInMemoryCircuitBreaker",
    "CircuitState",
    "InMemoryCircuitBreaker",
    "NoOpAsyncCircuitBreaker",
    "NoOpCircuitBreaker",
    # Retry
    "ExponentialBackoffRetry",
    "InMemoryRetryInFlightTracker",
    "NoOpRetry",
    "RetryContext",
    "RetryInFlightEntry",
    "RetryInFlightTracker",
    # Fallback
    "FallbackChain",
    "NoOpFallback",
    "OrderedFallbackChain",
    # Compensating transactions
    "CompensatingTransactionRegistry",
    # Validation
    "AllowlistedOperations",
    "GuardRailPolicy",
    "NoOpGate",
    "NoOpGuardRails",
    "OutputValidationGate",
    "PydanticValidationGate",
    "RULES_VERSION",
    "TrustBoundaryValidator",
    # Budget
    "AgentBudget",
    "BudgetWatchdog",
    "UnlimitedBudget",
    # Escalation
    "CheckpointStore",
    "EscalationHandlerLike",
    "EscalationRouter",
    "InMemoryCheckpointStore",
    "NoOpEscalationRouter",
    "NoOpHandler",
    "RecoveryDirective",
    # Persistence
    "AvailabilityMode",
    "Checkpoint",
    "NullProvider",
    "PersistedEnvelope",
    "PersistenceProvider",
    "ProviderCapabilities",
    "attestation",
    "get_active_provider",
    "set_active_provider",
    # Propagation
    "AsyncExceptionPropagationBus",
    "AsyncInMemoryBus",
    "AsyncInMemoryDLQ",
    "DeadLetterQueue",
    "ExceptionPropagationBus",
    "InMemoryBus",
    "InMemoryDLQ",
    "PropagationBufferFullError",
    "envelope_canonical_bytes",
    "envelope_canonical_sha256",
    "envelope_debug_repr",
    "envelope_from_json",
    "envelope_leaf_hash",
    "envelope_to_json",
    # Observability
    "ExceptionEventSink",
    "InMemoryMetricsCollector",
    "MetricsCollector",
    "NoOpMetricsCollector",
    "NoOpSink",
    "OTelExceptionAdapter",
    "PrometheusMetricsCollector",
    "StructuredLogEmitter",
    # Multi-agent
    "AgentSLAPolicy",
    "AgentOutcome",
    "ConsensusGate",
    "ConsensusNotReachedError",
    "FanOutOutcomeEnvelope",
    "OrchestratorExceptionRouter",
    "WorkerHandler",
    "call_parallel",
    "fan_out",
]
