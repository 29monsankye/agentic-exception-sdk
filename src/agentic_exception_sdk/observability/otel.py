"""OpenTelemetry adapter for exception envelopes.

Emits spans with GenAI semantic convention attributes following the
opentelemetry-semantic-conventions v1.29.0 spec. Each emit() call creates
a single "agent.exception" span that is immediately ended with ERROR status
for BatchSpanProcessor export.

Requires the ``otel`` optional extra:
``pip install agentic-exception-sdk[otel]``

Gracefully degrades when opentelemetry is not installed: the class can be
imported but raises ImportError on construction.

Attribute namespaces:
  gen_ai.*           — GenAI semantic conventions v1.29.0
  agent.exception.*  — SDK-specific exception attributes
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from agentic_exception_sdk.persistence.attestation import get_provider_capabilities
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope
from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator

_log = logging.getLogger(__name__)

__all__ = ["OTelExceptionAdapter"]

_OTEL_AVAILABLE = False
try:
    from opentelemetry.trace import StatusCode as _StatusCode
    _OTEL_AVAILABLE = True
except ImportError:
    pass


class OTelExceptionAdapter:
    """Emits exception envelopes as OTel spans with GenAI semantic attributes.

    Each emit() call creates a span named "agent.exception" on the configured
    TracerProvider. The span is immediately ended with ERROR status so that
    BatchSpanProcessor can export it.

    force_flush() delegates to the BatchSpanProcessor so that HARD_KILL spans
    are not lost when the process terminates.

    Args:
        tracer_provider: OTel TracerProvider to use for span creation.
        span_processor: Optional BatchSpanProcessor for force_flush() support.
            When provided, force_flush() delegates to it before HARD_KILL.

    Raises:
        ImportError: If opentelemetry-sdk is not installed.
    """

    def __init__(
        self,
        tracer_provider: Any,
        *,
        span_processor: Any | None = None,
    ) -> None:
        if not _OTEL_AVAILABLE:
            raise ImportError(
                "OTelExceptionAdapter requires "
                "'pip install agentic-exception-sdk[otel]'"
            )
        self._trust_boundary = TrustBoundaryValidator()
        self._tracer = tracer_provider.get_tracer(
            "agentic_exception_sdk",
            schema_url="https://opentelemetry.io/schemas/1.29.0",
        )
        self._span_processor = span_processor

    def _safe_scalar(self, value: object) -> object:
        if isinstance(value, str):
            return self._trust_boundary.safe_identifier(value)
        return value

    def _set_if_scalar(self, span: object, name: str, value: object) -> None:
        if isinstance(value, str | bool | int | float):
            span.set_attribute(name, value)  # type: ignore[attr-defined]

    @staticmethod
    def _first_scalar(mapping: Mapping[str, object], *keys: str) -> object | None:
        for key in keys:
            value = mapping.get(key)
            if value is not None:
                return value
        return None

    def emit(self, envelope: AgentExceptionEnvelope) -> None:
        """Emit the envelope as an OTel span with GenAI semantic attributes.

        Never raises — all internal errors are caught and logged at DEBUG level.

        Args:
            envelope: The classified exception envelope to emit.
        """
        if not _OTEL_AVAILABLE:
            return
        try:
            with self._tracer.start_as_current_span(
                "agent.exception",
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                # GenAI semantic conventions v1.29.0
                span.set_attribute("gen_ai.system", "agent_exception_sdk")
                span.set_attribute("gen_ai.operation.name", "agent.exception")
                safe_agent_id = self._trust_boundary.safe_identifier(envelope.agent_id)
                safe_error_type = self._trust_boundary.safe_identifier(envelope.error_type)
                # exception_id is SDK-generated and UUID-validated (not host-controlled),
                # so it is emitted raw: it is the primary span<->envelope correlation key
                # and must never be mangled by redaction patterns.
                safe_exception_id = envelope.exception_id
                span.set_attribute("gen_ai.agent.id", safe_agent_id)
                span.set_attribute("gen_ai.agent.name", safe_agent_id)
                span.set_attribute("error.type", safe_error_type)

                if envelope.tool_name is not None:
                    safe_tool_name = self._trust_boundary.safe_identifier(envelope.tool_name)
                    span.set_attribute("gen_ai.tool.name", safe_tool_name)
                    span.set_attribute("gen_ai.tool.call.id", safe_exception_id)
                if envelope.correlation_id is not None:
                    safe_correlation_id = self._trust_boundary.safe_identifier(
                        envelope.correlation_id
                    )
                    span.set_attribute("gen_ai.conversation.id", safe_correlation_id)

                context = envelope.context_snapshot.root
                response_id = self._first_scalar(context, "response_id", "gen_ai.response.id")
                response_model = self._first_scalar(context, "response_model", "model")
                finish_reasons = self._first_scalar(
                    context, "finish_reasons", "finish_reason", "gen_ai.response.finish_reasons"
                )
                input_tokens = self._first_scalar(context, "input_tokens", "usage.input_tokens")
                output_tokens = self._first_scalar(context, "output_tokens", "usage.output_tokens")
                total_tokens = self._first_scalar(context, "total_tokens", "usage.total_tokens")

                self._set_if_scalar(span, "gen_ai.response.id", self._safe_scalar(response_id))
                self._set_if_scalar(
                    span,
                    "gen_ai.response.model",
                    self._safe_scalar(response_model),
                )
                if isinstance(finish_reasons, Sequence) and not isinstance(finish_reasons, str):
                    span.set_attribute(
                        "gen_ai.response.finish_reasons",
                        [str(self._safe_scalar(item)) for item in finish_reasons],
                    )
                else:
                    self._set_if_scalar(
                        span,
                        "gen_ai.response.finish_reasons",
                        self._safe_scalar(finish_reasons),
                    )
                self._set_if_scalar(span, "gen_ai.usage.input_tokens", input_tokens)
                self._set_if_scalar(span, "gen_ai.usage.output_tokens", output_tokens)
                self._set_if_scalar(span, "gen_ai.usage.total_tokens", total_tokens)

                # SDK-specific exception attributes
                span.set_attribute("agent.exception.id", safe_exception_id)
                span.set_attribute("agent.exception.class", envelope.exception_class.value)
                span.set_attribute("agent.exception.level", envelope.suggested_recovery.name)
                span.set_attribute("agent.exception.source", envelope.source.value)
                span.set_attribute("agent.exception.error_type", safe_error_type)
                safe_message = self._trust_boundary.safe_exception_message(
                    ValueError(envelope.message)
                )
                span.set_attribute("agent.exception.message", safe_message)
                span.set_attribute("agent.exception.sdk_version", envelope.sdk_version)

                if envelope.tool_name is not None:
                    span.set_attribute("agent.exception.tool_name", safe_tool_name)
                if envelope.correlation_id is not None:
                    span.set_attribute(
                        "agent.exception.correlation_id",
                        safe_correlation_id,
                    )
                if envelope.lineage:
                    span.set_attribute(
                        "agent.exception.lineage",
                        ",".join(
                            self._trust_boundary.safe_identifier(item)
                            for item in envelope.lineage
                        ),
                    )

                provider_capabilities = get_provider_capabilities()
                if not provider_capabilities.durable:
                    span.set_attribute("sentirock.audit.degraded", True)
                    span.set_attribute("sentirock.provider.durable", False)

                span.set_status(_StatusCode.ERROR, safe_message)
        except Exception:
            _log.debug("OTelExceptionAdapter.emit failed; ignoring")

    def force_flush(self) -> None:
        """Flush the BatchSpanProcessor before a HARD_KILL termination.

        Silently skips if no span_processor was configured.
        """
        if self._span_processor is not None:
            try:
                self._span_processor.force_flush()
            except Exception:
                _log.debug("OTelExceptionAdapter.force_flush failed; ignoring")
