"""Standard exception envelope — the single propagation unit across all agent boundaries.

AgentExceptionEnvelope is frozen, serializable, and immutable. It must serialize only
with ``model_dump_json()`` / ``model_validate_json()`` in strict JSON mode. Pickle
serialization is forbidden. Non-finite floats (inf, -inf, nan) are rejected at the
envelope boundary.

When envelope JSON is used for hashing, deduplication, signatures, or DLQ keys,
canonicalize with an RFC 8785 JSON Canonicalization Scheme (JCS) library such as
``jcs``. ``json.dumps(..., sort_keys=True)`` is acceptable only for human-readable
debug output, not for security-sensitive operations.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator
from uuid_utils import uuid7

from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)

__all__ = ["AgentExceptionEnvelope", "SafeContextSnapshot", "SafeContextValue"]

_CLASS_TO_PERMITTED_LEVELS: dict[AgentExceptionClass, frozenset[EscalationLevel]] = {
    AgentExceptionClass.EXCEPTION: frozenset({
        EscalationLevel.L0_SELF_RETRY,
        EscalationLevel.L1_FALLBACK_PATH,
    }),
    AgentExceptionClass.ISSUE: frozenset({
        EscalationLevel.L2_CHECKPOINT_HANDOFF,
        EscalationLevel.L3_HUMAN_ESCALATION,
    }),
    AgentExceptionClass.HARD_KILL: frozenset({
        EscalationLevel.L4_SAFE_ABORT,
    }),
}

# Finite-float sentinel for Pydantic field annotation
SafeFiniteFloat = Annotated[float, Field(allow_inf_nan=False)]

SAFE_CONTEXT_MAX_DEPTH: int = 5
SAFE_CONTEXT_MAX_KEYS: int = 100

# Type alias for JSON-safe context values (documentation only — actual validation
# is performed by _check_node to avoid Pydantic v2 recursive schema generation).
SafeContextValue: TypeAlias = Any

# Log-safe identifier: lowercase alphanumeric, underscore, hyphen, 1-128 chars.
# Dots and colons are intentionally excluded because logfmt/syslog shippers
# treat them as field separators.
_LOG_SAFE_ID_RE: re.Pattern[str] = re.compile(r"^[a-z0-9_-]{1,128}$")


_SAFE_SCALAR_TYPES = (str, int, float, bool, type(None))


def _new_exception_id() -> str:
    """Return an interceptor-assigned UUIDv7 exception identifier."""
    return str(uuid7())


def _check_node(node: Any, depth: int) -> None:
    """Recursive tree walk enforcing depth, key-count, type, and finite-float constraints.

    Permitted leaf types: str, int, float (finite only), bool, None.
    Permitted container types: list, dict with str keys.
    All other types are rejected so the snapshot stays JSON-serializable.
    """
    if depth > SAFE_CONTEXT_MAX_DEPTH:
        raise ValueError(f"context_snapshot exceeds max depth {SAFE_CONTEXT_MAX_DEPTH}")
    if isinstance(node, dict):
        if len(node) > SAFE_CONTEXT_MAX_KEYS:
            raise ValueError(
                f"context_snapshot dict exceeds max keys {SAFE_CONTEXT_MAX_KEYS}"
            )
        for value in node.values():
            _check_node(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _check_node(item, depth + 1)
    elif isinstance(node, float):
        if node != node or node == float("inf") or node == float("-inf"):
            raise ValueError("non-finite float in context_snapshot")
    elif not isinstance(node, _SAFE_SCALAR_TYPES):
        raise ValueError(
            f"context_snapshot contains unsupported type {type(node).__name__!r}; "
            "only str, int, float, bool, None, list, and dict are permitted"
        )


class SafeContextSnapshot(RootModel[dict[str, Any]]):
    """JSON-safe, recursively validated context snapshot.

    Enforces:
    - Maximum nesting depth of 5 levels
    - Maximum 100 keys per dictionary node
    - No non-finite float values (inf, -inf, nan)
    - Only JSON-serializable scalar types, lists, and nested dicts

    This is not a free-form ``dict[str, Any]``. It must be constructed via the
    ``TrustBoundaryValidator.sanitize_context_snapshot()`` sanitizer before
    envelope construction so that host-provided state is bounded and redacted.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_recursive_depth_and_values(self) -> SafeContextSnapshot:
        _check_node(self.root, depth=0)
        return self


class AgentExceptionEnvelope(BaseModel):
    """The single propagation unit across all agent boundaries and DLQs.

    Frozen Pydantic v2 model — serializable, immutable, and hashable once
    constructed. Construction validates the class/escalation-level consistency
    constraint so misclassification is always a hard error, not a silent bug.

    Serialization contract: use ``model_dump_json()`` / ``model_validate_json()``
    only. Pickle is forbidden. Non-finite floats are rejected.

    Attributes:
        exception_id: Interceptor-assigned UUIDv7.
        agent_id: Log-safe identifier of the agent that raised.
        tool_name: Tool being called when the failure occurred, if applicable.
        exception_class: PRIMARY routing class: EXCEPTION / ISSUE / HARD_KILL.
        source: INFORMATIONAL source: MODEL / TOOL / ORCHESTRATION / DATA_ENV.
        error_type: Original Python exception class name.
        message: Sanitized, bounded (max 500 chars) description of the failure.
        context_snapshot: Sanitized agent state at the point of failure.
        suggested_recovery: Specific action within the class.
        occurred_at: UTC timestamp of the failure.
        correlation_id: End-to-end trace ID from the host project, if available.
        sdk_version: Semantic version of the SDK that created this envelope.
        attempt_count: Retry attempt count. Defaults to 1 when host does not track it.
        lineage: Agent propagation chain, starting with the originating agent_id.
    """

    model_config = ConfigDict(frozen=True)

    exception_id: str = Field(default_factory=_new_exception_id)
    agent_id: str
    tool_name: str | None = None
    exception_class: AgentExceptionClass
    source: ExceptionSource
    error_type: str
    message: str = Field(max_length=500)
    context_snapshot: SafeContextSnapshot
    suggested_recovery: EscalationLevel
    occurred_at: datetime
    correlation_id: str | None = None
    sdk_version: str = Field(default="1.1.0", pattern=r"^\d+\.\d+\.\d+$")
    attempt_count: int = Field(default=1, ge=1)
    lineage: list[str] = Field(default_factory=list)

    @field_validator("exception_id", mode="before")
    @classmethod
    def _validate_exception_id(cls, value: str | None) -> str:
        """Require exception IDs to be valid UUID strings."""
        if not isinstance(value, str) or not value:
            raise ValueError("exception_id must be a valid UUID string")
        try:
            UUID(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("exception_id must be a valid UUID string") from exc
        return value

    @field_validator("agent_id", "correlation_id", mode="before")
    @classmethod
    def _validate_log_safe_identifier(cls, value: str | None) -> str | None:
        """Reject identifiers with characters that could poison logfmt/syslog shippers.

        Args:
            value: Raw identifier string or None.

        Returns:
            The validated identifier unchanged.

        Raises:
            ValueError: If the identifier contains disallowed characters or exceeds 128 chars.
        """
        if value is None:
            return None
        if not _LOG_SAFE_ID_RE.fullmatch(value):
            raise ValueError(
                "identifier must be 1-128 characters containing only "
                "lowercase letters, digits, underscore, or hyphen"
            )
        return value

    @model_validator(mode="after")
    def _validate_class_level_consistency(self) -> AgentExceptionEnvelope:
        """Enforce that class and escalation level are a permitted combination.

        This validator must run after all field validators so it sees the final
        enum values. Do not change ``mode='after'`` without re-verifying frozen
        model invariants and class/level validation order.

        Raises:
            ValueError: If suggested_recovery is not permitted for exception_class.
        """
        permitted = _CLASS_TO_PERMITTED_LEVELS[self.exception_class]
        if self.suggested_recovery not in permitted:
            raise ValueError(
                f"EscalationLevel {self.suggested_recovery!r} is not permitted "
                f"for AgentExceptionClass {self.exception_class!r}. "
                f"Permitted levels: {[lv.name for lv in permitted]}"
            )
        return self


# model_rebuild() ensures forward references in AgentExceptionEnvelope field
# annotations (e.g. SafeContextSnapshot) are fully resolved.
AgentExceptionEnvelope.model_rebuild()
