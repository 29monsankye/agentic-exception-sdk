"""ResilienceBundle — injectable container for all SDK components.

All fields default to safe NoOp implementations so a bundle with defaults is
immediately usable without any configuration. Host projects replace individual
components via constructor injection — never via global state or monkey-patching.

Zero host imports: this module only imports from within the SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentic_exception_sdk.budget.models import AgentBudget
from agentic_exception_sdk.budget.watchdog import BudgetWatchdog
from agentic_exception_sdk.escalation.router import NoOpEscalationRouter
from agentic_exception_sdk.observability.metrics import (
    MetricsCollector,
    NoOpMetricsCollector,
)
from agentic_exception_sdk.observability.sink import NoOpSink
from agentic_exception_sdk.propagation.bus import InMemoryBus
from agentic_exception_sdk.propagation.dlq import InMemoryDLQ
from agentic_exception_sdk.resilience.circuit_breaker import (
    NoOpCircuitBreaker,
)
from agentic_exception_sdk.resilience.retry import NoOpRetry
from agentic_exception_sdk.taxonomy.classifier import ExceptionClassifier
from agentic_exception_sdk.validation.gates import NoOpGate
from agentic_exception_sdk.validation.guard_rails import NoOpGuardRails
from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator

if TYPE_CHECKING:
    from opentelemetry.metrics import MeterProvider

__all__ = ["ResilienceBundle"]


@dataclass
class ResilienceBundle:
    """All SDK components in one injectable container.

    Defaults every component to a safe no-op implementation. Host projects
    replace individual fields to enable specific capabilities:

        bundle = ResilienceBundle(
            circuit_breaker=InMemoryCircuitBreaker(failure_threshold=3),
            exception_sink=OTelExceptionAdapter(tracer_provider),
        )

    Attributes:
        classifier: Maps exceptions to (class, source, level) tuples.
        trust_boundary: Sanitizes all host-provided strings and snapshots.
        guard_rails: Enforces tool call allowlist. NoOpGuardRails by default.
        agent_budget: Tracks and enforces resource budgets. Unlimited by default.
        retry_policy: Retry strategy. Single-attempt (no retry) by default.
        circuit_breaker: Sync circuit breaker. NoOp (pass-through) by default.
        output_validation_gate: Validates tool outputs. Pass-through by default.
        exception_sink: Observability sink. Discards all envelopes by default.
        metrics_collector: Metrics collector. NoOp by default.
        meter_provider: Optional OTel MeterProvider for tool latency histograms.
        propagation_bus: Propagation bus. Bounded in-memory (1000 envelopes) by default.
        dlq: Dead-letter queue for HARD_KILL envelopes. InMemoryDLQ by default.
        escalation_router: Routes envelopes to handlers. NoOp by default.
        recovery_policy: Optional post-classification recovery policy. None by default.
        async_circuit_breaker: Optional async circuit breaker. None by default,
            meaning async_resilient() falls back to bundle.circuit_breaker.
    """

    classifier: ExceptionClassifier = field(default_factory=ExceptionClassifier)
    trust_boundary: TrustBoundaryValidator = field(
        default_factory=TrustBoundaryValidator
    )
    guard_rails: Any = field(default_factory=NoOpGuardRails)
    agent_budget: BudgetWatchdog = field(
        default_factory=lambda: BudgetWatchdog(AgentBudget())
    )
    retry_policy: Any = field(default_factory=NoOpRetry)
    circuit_breaker: Any = field(default_factory=NoOpCircuitBreaker)
    output_validation_gate: Any = field(default_factory=NoOpGate)
    exception_sink: Any = field(default_factory=NoOpSink)
    metrics_collector: MetricsCollector = field(default_factory=NoOpMetricsCollector)
    meter_provider: MeterProvider | None = None
    propagation_bus: Any = field(default_factory=InMemoryBus)
    dlq: Any = field(default_factory=InMemoryDLQ)
    escalation_router: Any = field(default_factory=NoOpEscalationRouter)
    recovery_policy: Any | None = None
    async_circuit_breaker: Any | None = None
    _latency_histogram: Any | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        """Cache optional OTel instruments once at bundle construction."""
        if self.meter_provider is None:
            self._latency_histogram = None
            return
        meter = self.meter_provider.get_meter("agentic_exception_sdk")
        self._latency_histogram = meter.create_histogram(
            "agent_tool_call_duration_seconds",
            unit="s",
            description="Per-call tool duration by agent and tool name",
        )
