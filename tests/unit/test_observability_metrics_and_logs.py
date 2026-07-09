"""Tests for SDK observability sinks, structured logs, and metrics collectors."""

from __future__ import annotations

import builtins
import logging
import sys
import types

import pytest

import agentic_exception_sdk.observability.metrics as metrics_module
import agentic_exception_sdk.observability.structured_log as structured_log_module
from agentic_exception_sdk.observability.metrics import (
    InMemoryMetricsCollector,
    MetricsCollector,
    NoOpMetricsCollector,
    PrometheusMetricsCollector,
)
from agentic_exception_sdk.observability.sink import ExceptionEventSink, NoOpSink
from agentic_exception_sdk.observability.structured_log import StructuredLogEmitter


@pytest.mark.parametrize(
    ("fixture_name", "expected_level", "expected_class"),
    [
        ("exception_envelope", logging.WARNING, "exception"),
        ("issue_envelope", logging.ERROR, "issue"),
        ("hard_kill_envelope", logging.CRITICAL, "hard_kill"),
    ],
)
def test_structured_log_emitter_maps_levels_and_logfmt(
    request,
    caplog,
    fixture_name,
    expected_level,
    expected_class,
):
    envelope = request.getfixturevalue(fixture_name)
    logger_name = f"test.agentic.events.{fixture_name}"
    emitter = StructuredLogEmitter(logger_name=logger_name, use_structlog=False)

    with caplog.at_level(logging.WARNING, logger=logger_name):
        emitter.emit(envelope)

    record = caplog.records[-1]
    assert record.levelno == expected_level
    assert f"exception_id={envelope.exception_id}" in record.message
    assert f"exception_class={expected_class}" in record.message
    assert f"escalation_level={envelope.suggested_recovery.name}" in record.message
    assert f"source={envelope.source.value}" in record.message


def test_structured_log_emitter_includes_optional_fields(caplog, exception_envelope):
    envelope = exception_envelope.model_copy(
        update={
            "tool_name": "search",
            "correlation_id": "corr-123",
            "lineage": ["root", "child"],
        }
    )
    logger_name = "test.agentic.events.optional"
    emitter = StructuredLogEmitter(logger_name=logger_name, use_structlog=False)

    with caplog.at_level(logging.WARNING, logger=logger_name):
        emitter.emit(envelope)

    message = caplog.records[-1].message
    assert "tool_name=search" in message
    assert "correlation_id=corr-123" in message
    assert "lineage=root,child" in message


def test_structured_log_emitter_falls_back_when_structlog_emit_fails(
    monkeypatch,
    caplog,
    issue_envelope,
):
    class FailingStructlogLogger:
        def error(self, *args, **kwargs):
            raise RuntimeError("structlog unavailable at emit time")

    class FakeStructlog:
        @staticmethod
        def get_logger(name):
            return FailingStructlogLogger()

    monkeypatch.setattr(structured_log_module, "_STRUCTLOG_AVAILABLE", True)
    monkeypatch.setattr(structured_log_module, "structlog", FakeStructlog(), raising=False)
    logger_name = "test.agentic.events.structlog-fallback"
    emitter = StructuredLogEmitter(logger_name=logger_name, use_structlog=True)

    with caplog.at_level(logging.ERROR, logger=logger_name):
        emitter.emit(issue_envelope)

    assert caplog.records[-1].levelno == logging.ERROR
    assert f"exception_id={issue_envelope.exception_id}" in caplog.records[-1].message


def test_structured_log_force_flush_is_noop():
    assert StructuredLogEmitter().force_flush() is None


def test_noop_sink_satisfies_exception_event_sink(exception_envelope):
    sink = NoOpSink()

    assert isinstance(sink, ExceptionEventSink)
    assert sink.emit(exception_envelope) is None
    assert sink.force_flush() is None


def test_noop_metrics_collector_satisfies_protocol(exception_envelope):
    collector = NoOpMetricsCollector()

    assert isinstance(collector, MetricsCollector)
    assert collector.record_exception(exception_envelope) is None
    assert collector.record_hard_kill("agent-payment") is None
    assert collector.record_retry("agent-payment") is None
    assert collector.record_budget_exhausted() is None


def test_in_memory_metrics_collector_counts_labels_and_totals(
    exception_envelope,
    hard_kill_envelope,
):
    collector = InMemoryMetricsCollector()

    collector.record_exception(exception_envelope)
    collector.record_exception(exception_envelope)
    collector.record_exception(hard_kill_envelope)
    collector.record_hard_kill("agent-payment")
    collector.record_retry("agent-payment")
    collector.record_retry("agent-payment")
    collector.record_budget_exhausted()

    exception_label = (
        f"{exception_envelope.exception_class.value}:"
        f"{exception_envelope.suggested_recovery.name}:"
        f"{exception_envelope.source.value}"
    )
    hard_kill_label = (
        f"{hard_kill_envelope.exception_class.value}:"
        f"{hard_kill_envelope.suggested_recovery.name}:"
        f"{hard_kill_envelope.source.value}"
    )
    assert collector.exception_counts[exception_label] == 2
    assert collector.exception_counts[hard_kill_label] == 1
    assert collector.hard_kill_total == 1
    assert collector.retry_total == 2
    assert collector.budget_exhausted_total == 1


def test_prometheus_metrics_collector_raises_clear_import_error(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "prometheus_client":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delattr(metrics_module, "CollectorRegistry", raising=False)
    monkeypatch.delattr(metrics_module, "Counter", raising=False)

    with pytest.raises(ImportError, match="agentic-exception-sdk\\[prometheus\\]"):
        PrometheusMetricsCollector()


def test_prometheus_metrics_collector_records_with_private_registry(
    monkeypatch,
    exception_envelope,
):
    counters = {}

    class FakeCollectorRegistry:
        pass

    class FakeCounter:
        def __init__(self, name, description, labels=None, registry=None):
            self.name = name
            self.description = description
            self.label_names = labels or []
            self.registry = registry
            self.total = 0
            self.label_values = []
            counters[name] = self

        def labels(self, **kwargs):
            self.label_values.append(kwargs)
            return self

        def inc(self):
            self.total += 1

    fake_prometheus = types.SimpleNamespace(
        CollectorRegistry=FakeCollectorRegistry,
        Counter=FakeCounter,
    )
    monkeypatch.setitem(sys.modules, "prometheus_client", fake_prometheus)

    collector = PrometheusMetricsCollector()
    collector.record_exception(exception_envelope)
    collector.record_hard_kill("agent-payment")
    collector.record_retry("agent-payment")
    collector.record_budget_exhausted()

    assert counters["agent_exceptions_total"].label_values == [
        {
            "agent_id": exception_envelope.agent_id,
            "exception_class": exception_envelope.exception_class.value,
            "escalation_level": exception_envelope.suggested_recovery.name,
            "source": exception_envelope.source.value,
        }
    ]
    assert counters["agent_exceptions_total"].total == 1
    assert counters["agent_hard_kills_total"].label_values == [
        {"agent_id": "agent-payment"}
    ]
    assert counters["agent_hard_kills_total"].total == 1
    assert counters["agent_retries_total"].label_values == [
        {"agent_id": "agent-payment"}
    ]
    assert counters["agent_retries_total"].total == 1
    assert counters["agent_budget_exhausted_total"].total == 1
