"""Structured log emitter for exception envelopes.

Emits logfmt-compatible key=value log records via Python's stdlib logging.
If structlog is available and use_structlog=True, uses structlog's bound-logger
for richer context propagation.

The emitter never logs raw PII — only envelope fields that have passed through
TrustBoundaryValidator are emitted.

Log level mapping:
- EXCEPTION -> WARNING
- ISSUE     -> ERROR
- HARD_KILL -> CRITICAL
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

_log = logging.getLogger(__name__)

__all__ = ["StructuredLogEmitter"]

_STRUCTLOG_AVAILABLE = False
try:
    import structlog
    _STRUCTLOG_AVAILABLE = True
except ImportError:
    pass

_LEVEL_NAME_MAP = {
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "critical",
}


class StructuredLogEmitter:
    """Emits exception envelopes as structured log records.

    Uses structlog when available for richer context binding. Falls back to
    stdlib logging in logfmt key=value format when structlog is not installed
    or use_structlog=False.

    Args:
        logger_name: Logger name. Default: "agentic_exception_sdk.events".
        use_structlog: If True, use structlog when available. Default True.
    """

    def __init__(
        self,
        logger_name: str = "agentic_exception_sdk.events",
        *,
        use_structlog: bool = True,
    ) -> None:
        self._logger_name = logger_name
        self._use_structlog = use_structlog and _STRUCTLOG_AVAILABLE

    def emit(self, envelope: AgentExceptionEnvelope) -> None:
        """Emit the envelope as a structured log record.

        Args:
            envelope: The classified exception envelope to log.
        """
        from agentic_exception_sdk.taxonomy.enums import AgentExceptionClass

        level_map = {
            AgentExceptionClass.EXCEPTION: logging.WARNING,
            AgentExceptionClass.ISSUE: logging.ERROR,
            AgentExceptionClass.HARD_KILL: logging.CRITICAL,
        }
        level = level_map.get(envelope.exception_class, logging.ERROR)

        fields: dict[str, Any] = {
            "exception_id": envelope.exception_id,
            "agent_id": envelope.agent_id,
            "exception_class": envelope.exception_class.value,
            "escalation_level": envelope.suggested_recovery.name,
            "source": envelope.source.value,
            "error_type": envelope.error_type,
            "message": envelope.message,
            "sdk_version": envelope.sdk_version,
        }
        if envelope.tool_name is not None:
            fields["tool_name"] = envelope.tool_name
        if envelope.correlation_id is not None:
            fields["correlation_id"] = envelope.correlation_id
        if envelope.lineage:
            fields["lineage"] = ",".join(envelope.lineage)

        if self._use_structlog:
            try:
                logger = structlog.get_logger(self._logger_name)
                log_fn_name = _LEVEL_NAME_MAP.get(level, "error")
                log_fn = getattr(logger, log_fn_name)
                log_fn("agent.exception", **fields)
                return
            except Exception as exc:
                _log.debug("structlog emit failed; falling back to stdlib logging: %s", exc)

        stdlib_logger = logging.getLogger(self._logger_name)
        logfmt = " ".join(f"{k}={v}" for k, v in fields.items())
        stdlib_logger.log(level, logfmt)

    def force_flush(self) -> None:
        """No-op flush — stdlib logging and structlog flush automatically."""
        return None
