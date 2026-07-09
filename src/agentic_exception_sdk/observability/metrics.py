"""Metrics collector protocol and implementations.

InMemoryMetricsCollector is used for tests and development.
PrometheusMetricsCollector requires the ``prometheus`` optional extra.

Required SDK counters:
- agent_exceptions_total{agent_id, exception_class, escalation_level, source}
- agent_hard_kills_total{agent_id}
- agent_retries_total{agent_id}
- agent_budget_exhausted_total
- propagation_bus_publish_failures_total (tracked on InMemoryBus directly)
- dlq_dropped_oldest_total (tracked on InMemoryDLQ directly)
- tool_name_canonicalization_modified_total (tracked on TrustBoundaryValidator)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "InMemoryMetricsCollector",
    "MetricsCollector",
    "NoOpMetricsCollector",
    "PrometheusMetricsCollector",
]


@runtime_checkable
class MetricsCollector(Protocol):
    """Protocol for SDK metrics collection.

    All methods must be safe to call from both sync and async contexts.
    Implementations must never raise — swallow all internal errors.
    """

    def record_exception(self, envelope: AgentExceptionEnvelope) -> None:
        """Increment agent_exceptions_total with envelope labels.

        Args:
            envelope: The classified exception envelope.
        """
        ...

    def record_hard_kill(self, agent_id: str | None = None) -> None:
        """Increment agent_hard_kills_total."""
        ...

    def record_retry(self, agent_id: str | None = None) -> None:
        """Increment agent_retries_total."""
        ...

    def record_budget_exhausted(self) -> None:
        """Increment agent_budget_exhausted_total."""
        ...


class NoOpMetricsCollector:
    """Metrics collector that silently discards all observations."""

    def record_exception(self, envelope: AgentExceptionEnvelope) -> None:
        return None

    def record_hard_kill(self, agent_id: str | None = None) -> None:
        return None

    def record_retry(self, agent_id: str | None = None) -> None:
        return None

    def record_budget_exhausted(self) -> None:
        return None


@dataclass
class InMemoryMetricsCollector:
    """Thread-unsafe in-memory metrics collector for testing and development.

    Not suitable for production — counters are plain ints with no locking.
    Use PrometheusMetricsCollector for production.

    Attributes:
        exception_counts: Counter keyed by "class:level:source" label string.
        hard_kill_total: Total HARD_KILL terminations recorded.
        retry_total: Total retry attempts recorded.
        budget_exhausted_total: Total budget exhausted events.
    """

    exception_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    hard_kill_total: int = 0
    retry_total: int = 0
    budget_exhausted_total: int = 0

    def record_exception(self, envelope: AgentExceptionEnvelope) -> None:
        label = (
            f"{envelope.exception_class.value}"
            f":{envelope.suggested_recovery.name}"
            f":{envelope.source.value}"
        )
        self.exception_counts[label] += 1

    def record_hard_kill(self, agent_id: str | None = None) -> None:
        self.hard_kill_total += 1

    def record_retry(self, agent_id: str | None = None) -> None:
        self.retry_total += 1

    def record_budget_exhausted(self) -> None:
        self.budget_exhausted_total += 1


class PrometheusMetricsCollector:
    """Prometheus metrics collector using prometheus_client.

    Requires the ``prometheus`` optional extra:
    ``pip install agentic-exception-sdk[prometheus]``.

    Uses a dedicated CollectorRegistry to avoid polluting the default Prometheus
    registry in multi-tenant environments.

    Use stable logical agent_id values such as "agent-payment". Dynamic IDs
    such as UUIDs create high-cardinality metrics and can exhaust Prometheus memory.

    Args:
        registry: Optional prometheus_client.CollectorRegistry. A new private
                  registry is created when not provided.

    Raises:
        ImportError: If prometheus_client is not installed.
    """

    def __init__(self, registry: Any | None = None) -> None:
        try:
            from prometheus_client import CollectorRegistry, Counter
        except ImportError as exc:
            raise ImportError(
                "PrometheusMetricsCollector requires "
                "'pip install agentic-exception-sdk[prometheus]'"
            ) from exc

        reg = registry or CollectorRegistry()
        self._agent_exceptions_total = Counter(
            "agent_exceptions_total",
            "Total agent exceptions classified by class, level, and source",
            ["agent_id", "exception_class", "escalation_level", "source"],
            registry=reg,
        )
        self._agent_hard_kills_total = Counter(
            "agent_hard_kills_total",
            "Total HARD_KILL agent terminations",
            ["agent_id"],
            registry=reg,
        )
        self._agent_retries_total = Counter(
            "agent_retries_total",
            "Total retry attempts across all tool calls",
            ["agent_id"],
            registry=reg,
        )
        self._agent_budget_exhausted_total = Counter(
            "agent_budget_exhausted_total",
            "Total budget exhausted events",
            registry=reg,
        )

    def record_exception(self, envelope: AgentExceptionEnvelope) -> None:
        self._agent_exceptions_total.labels(
            agent_id=envelope.agent_id,
            exception_class=envelope.exception_class.value,
            escalation_level=envelope.suggested_recovery.name,
            source=envelope.source.value,
        ).inc()

    def record_hard_kill(self, agent_id: str | None = None) -> None:
        self._agent_hard_kills_total.labels(
            agent_id=agent_id or "unknown",
        ).inc()

    def record_retry(self, agent_id: str | None = None) -> None:
        self._agent_retries_total.labels(
            agent_id=agent_id or "unknown",
        ).inc()

    def record_budget_exhausted(self) -> None:
        self._agent_budget_exhausted_total.inc()
