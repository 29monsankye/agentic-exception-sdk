"""Tests for serialization protocol helpers and observability adapters."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import agentic_exception_sdk.observability.otel as otel_module
from agentic_exception_sdk.observability.otel import OTelExceptionAdapter
from agentic_exception_sdk.propagation.protocol import (
    envelope_canonical_bytes,
    envelope_canonical_sha256,
    envelope_debug_repr,
    envelope_leaf_hash,
)
from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope, SafeContextSnapshot


def make_env(**kwargs):
    defaults = {
        "agent_id": "agent-a",
        "tool_name": "search-flights",
        "exception_class": AgentExceptionClass.EXCEPTION,
        "source": ExceptionSource.MODEL,
        "error_type": "TimeoutError",
        "message": "timeout",
        "context_snapshot": SafeContextSnapshot(
            {
                "response_id": "resp-1",
                "response_model": "gpt-test",
                "finish_reason": "error",
                "input_tokens": 11,
                "output_tokens": 22,
                "total_tokens": 33,
            }
        ),
        "suggested_recovery": EscalationLevel.L0_SELF_RETRY,
        "occurred_at": datetime.now(UTC),
        "correlation_id": "corr-1",
    }
    defaults.update(kwargs)
    return AgentExceptionEnvelope(**defaults)


def test_envelope_canonical_bytes_are_stable_and_not_debug_repr():
    env = make_env()
    canonical = envelope_canonical_bytes(env)
    assert isinstance(canonical, bytes)
    assert envelope_canonical_sha256(env) == envelope_canonical_sha256(env)
    assert canonical != envelope_debug_repr(env).encode("utf-8")


def test_envelope_canonical_sha256_excludes_attempt_count_only():
    env = make_env(sdk_version="1.0.0", attempt_count=1)
    retried = env.model_copy(update={"attempt_count": 3})
    next_version = env.model_copy(update={"sdk_version": "1.0.1"})

    assert envelope_canonical_sha256(env) == envelope_canonical_sha256(retried)
    assert envelope_canonical_sha256(env) != envelope_canonical_sha256(next_version)


def test_envelope_leaf_hash_includes_attempt_count():
    env = make_env(attempt_count=1)
    retried = env.model_copy(update={"attempt_count": 3})

    assert envelope_canonical_sha256(env) == envelope_canonical_sha256(retried)
    assert envelope_leaf_hash(env) != envelope_leaf_hash(retried)


def test_otel_emits_genai_attributes():
    pytest.importorskip("opentelemetry")

    class Span:
        def __init__(self):
            self.attributes = {}

        def set_attribute(self, name, value):
            self.attributes[name] = value

        def set_status(self, *args):
            self.status = args

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Tracer:
        def __init__(self):
            self.span = Span()

        def start_as_current_span(self, *args, **kwargs):
            return self.span

    class Provider:
        def __init__(self):
            self.tracer = Tracer()

        def get_tracer(self, *args, **kwargs):
            return self.tracer

    provider = Provider()
    env = make_env()
    OTelExceptionAdapter(provider).emit(env)

    attrs = provider.tracer.span.attributes
    assert attrs["error.type"] == "TimeoutError"
    assert attrs["gen_ai.tool.name"] == "search-flights"
    assert attrs["gen_ai.tool.call.id"] == env.exception_id
    assert attrs["gen_ai.agent.id"] == "agent-a"
    assert attrs["gen_ai.agent.name"] == "agent-a"
    assert attrs["gen_ai.conversation.id"] == "corr-1"
    assert attrs["gen_ai.response.id"] == "resp-1"
    assert attrs["gen_ai.response.model"] == "gpt-test"
    assert attrs["gen_ai.usage.input_tokens"] == 11
    assert attrs["gen_ai.usage.output_tokens"] == 22
    assert attrs["gen_ai.usage.total_tokens"] == 33
    assert attrs["sentirock.audit.degraded"] is True
    assert attrs["sentirock.provider.durable"] is False


def test_otel_sanitizes_identifier_attributes():
    pytest.importorskip("opentelemetry")

    class Span:
        def __init__(self):
            self.attributes = {}

        def set_attribute(self, name, value):
            self.attributes[name] = value

        def set_status(self, *args):
            self.status = args

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Tracer:
        def __init__(self):
            self.span = Span()

        def start_as_current_span(self, *args, **kwargs):
            return self.span

    class Provider:
        def __init__(self):
            self.tracer = Tracer()

        def get_tracer(self, *args, **kwargs):
            return self.tracer

    raw_agent_id = "a" * 300
    raw_tool_name = "search password=supersecretpassword123"
    env = AgentExceptionEnvelope.model_construct(
        exception_id="00000000-0000-7000-8000-000000000001",
        agent_id=raw_agent_id,
        tool_name=raw_tool_name,
        exception_class=AgentExceptionClass.EXCEPTION,
        source=ExceptionSource.MODEL,
        error_type="TimeoutError",
        message="timeout",
        context_snapshot=SafeContextSnapshot({}),
        suggested_recovery=EscalationLevel.L0_SELF_RETRY,
        occurred_at=datetime.now(UTC),
        correlation_id=None,
        sdk_version="1.0.0",
        attempt_count=1,
        lineage=[raw_agent_id],
    )

    provider = Provider()
    OTelExceptionAdapter(provider).emit(env)

    attrs = provider.tracer.span.attributes
    assert len(attrs["gen_ai.agent.id"]) == 256
    assert attrs["gen_ai.agent.id"].endswith("[TRUNCATED]")
    assert raw_agent_id not in attrs.values()
    assert "supersecretpassword123" not in attrs["gen_ai.tool.name"]
    assert "supersecretpassword123" not in attrs["agent.exception.tool_name"]


def test_otel_adapter_construction_raises_when_otel_unavailable(monkeypatch):
    class Provider:
        def get_tracer(self, *args, **kwargs):
            raise AssertionError("provider should not be touched when OTel is unavailable")

    monkeypatch.setattr(otel_module, "_OTEL_AVAILABLE", False)

    with pytest.raises(ImportError, match="agentic-exception-sdk\\[otel\\]"):
        OTelExceptionAdapter(Provider())
