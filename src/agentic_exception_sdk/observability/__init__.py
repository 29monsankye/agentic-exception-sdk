"""Observability: event sink, OTel adapter, metrics, and structured logging."""

from __future__ import annotations

from agentic_exception_sdk.observability.metrics import (
    InMemoryMetricsCollector,
    MetricsCollector,
    NoOpMetricsCollector,
    PrometheusMetricsCollector,
)
from agentic_exception_sdk.observability.otel import OTelExceptionAdapter
from agentic_exception_sdk.observability.sink import ExceptionEventSink, NoOpSink
from agentic_exception_sdk.observability.structured_log import StructuredLogEmitter

__all__ = [
    "ExceptionEventSink",
    "InMemoryMetricsCollector",
    "MetricsCollector",
    "NoOpMetricsCollector",
    "NoOpSink",
    "OTelExceptionAdapter",
    "PrometheusMetricsCollector",
    "StructuredLogEmitter",
]
