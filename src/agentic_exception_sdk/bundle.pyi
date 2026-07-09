from _typeshed import Incomplete
from agentic_exception_sdk.budget.watchdog import BudgetWatchdog
from agentic_exception_sdk.escalation.router import NoOpEscalationRouter
from agentic_exception_sdk.observability.metrics import MetricsCollector, NoOpMetricsCollector
from agentic_exception_sdk.observability.sink import NoOpSink
from agentic_exception_sdk.propagation.bus import InMemoryBus
from agentic_exception_sdk.propagation.dlq import InMemoryDLQ
from agentic_exception_sdk.resilience.circuit_breaker import NoOpCircuitBreaker
from agentic_exception_sdk.resilience.retry import NoOpRetry
from agentic_exception_sdk.taxonomy.classifier import ExceptionClassifier
from agentic_exception_sdk.validation.gates import NoOpGate
from agentic_exception_sdk.validation.guard_rails import NoOpGuardRails
from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator
from dataclasses import dataclass, field
from opentelemetry.metrics import MeterProvider
from typing import Any

__all__ = ['ResilienceBundle']

@dataclass
class ResilienceBundle:
    classifier: ExceptionClassifier = field(default_factory=ExceptionClassifier)
    trust_boundary: TrustBoundaryValidator = field(default_factory=TrustBoundaryValidator)
    guard_rails: Any = field(default_factory=NoOpGuardRails)
    agent_budget: BudgetWatchdog = field(default_factory=Incomplete)
    retry_policy: Any = field(default_factory=NoOpRetry)
    circuit_breaker: Any = field(default_factory=NoOpCircuitBreaker)
    output_validation_gate: Any = field(default_factory=NoOpGate)
    exception_sink: Any = field(default_factory=NoOpSink)
    metrics_collector: MetricsCollector = field(default_factory=NoOpMetricsCollector)
    meter_provider: MeterProvider | None = ...
    propagation_bus: Any = field(default_factory=InMemoryBus)
    dlq: Any = field(default_factory=InMemoryDLQ)
    escalation_router: Any = field(default_factory=NoOpEscalationRouter)
    recovery_policy: Any | None = ...
    async_circuit_breaker: Any | None = ...
    def __post_init__(self) -> None: ...
