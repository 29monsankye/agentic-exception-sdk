"""SDK-owned exception types.

AgentHardKillError and SecurityViolationError inherit from BaseException so they
cannot be swallowed by bare ``except Exception`` handlers anywhere in the call stack.
Host executors must catch AgentHardKillError explicitly at the outermost task boundary
and must isolate agent tasks from unrelated worker tenants in FastAPI/Uvicorn, Celery,
LangGraph, Temporal, and custom executors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "AgentHardKillError",
    "BudgetExhaustedError",
    "BudgetWarningError",
    "CircuitBreakerStateUnavailableError",
    "CompensationPartialFailureError",
    "FallbackCapableError",
    "GuardRailViolationError",
    "PromptInjectionError",
    "SLAViolationError",
    "SecurityViolationError",
    "StateCorruptionError",
    "ToolKindMismatchError",
    "ValidationGateError",
]


class AgentHardKillError(BaseException):
    """Raised when exception classification is HARD_KILL.

    Inherits from BaseException, not Exception, so it propagates through all
    ``except Exception`` handlers until it reaches the outermost executor boundary.

    The ``args`` tuple contains only the opaque ``exception_id`` string — never
    envelope content. Human-readable text is rendered only through ``__str__``.
    Pickling, shallow copy, and deep copy are all explicitly blocked.

    Args:
        envelope: The AgentExceptionEnvelope describing the terminal failure.
    """

    def __init__(self, envelope: AgentExceptionEnvelope) -> None:
        self.envelope = envelope
        self.__suppress_context__ = True
        super().__init__(envelope.exception_id)

    def __str__(self) -> str:
        return (
            f"HARD KILL [{self.envelope.exception_id}] "
            f"{self.envelope.source.value}: {self.envelope.message}"
        )

    def __repr__(self) -> str:
        return f"AgentHardKillError(exception_id={self.envelope.exception_id!r})"

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("AgentHardKillError is not pickle-serializable")

    def __deepcopy__(self, memo: dict[int, object]) -> AgentHardKillError:
        raise TypeError("AgentHardKillError cannot be deep-copied")

    def __copy__(self) -> AgentHardKillError:
        raise TypeError("AgentHardKillError cannot be shallow-copied")


class SecurityViolationError(BaseException):
    """Base class for security and compliance policy violations.

    Inherits from BaseException to bypass standard except-Exception handlers.
    """


class PromptInjectionError(SecurityViolationError):
    """Raised when a model or guardrail detects prompt injection.

    This is a first-class SDK error because prompt injection is a canonical
    HARD_KILL trigger. Classifies as HARD_KILL / L4_SAFE_ABORT.
    """


class StateCorruptionError(BaseException):
    """Raised when agent state is irrecoverably inconsistent.

    Classifies as HARD_KILL / L4_SAFE_ABORT.
    """


class CompensationPartialFailureError(BaseException):
    """Raised when one or more compensating transactions fail during rollback.

    A partially failed rollback leaves the system in an inconsistent state that
    cannot be automatically recovered safely.
    Classifies as HARD_KILL / L4_SAFE_ABORT.
    """

    def __init__(self, failed_step_ids: list[str]) -> None:
        self.failed_step_ids = failed_step_ids
        super().__init__(f"compensation failed for steps: {failed_step_ids}")


class FallbackCapableError(Exception):
    """Subclass this to signal that a fallback path is intentionally available.

    The classifier uses ``isinstance(exc, FallbackCapableError)`` — duck-typing
    such as ``getattr(exc, 'has_fallback', False)`` is explicitly NOT supported.
    Classifies as EXCEPTION / L1_FALLBACK_PATH when raised from a ValueError or TypeError.
    """


class ToolKindMismatchError(TypeError):
    """Raised when sync and async resilient wrappers receive the wrong callable kind.

    ``resilient()`` rejects coroutine functions.
    ``async_resilient()`` rejects non-coroutine functions.
    Also raised when ``timeout_seconds`` is set on ``resilient()`` without
    setting ``allow_sync_llm_timeout=True``.
    """


class GuardRailViolationError(SecurityViolationError):
    """Raised when a tool call violates the allowlist constraint.

    Classifies as HARD_KILL / L4_SAFE_ABORT because continuing would violate a
    host-defined policy boundary.

    Args:
        canonical_tool_name: The normalized tool name that triggered the violation.
    """

    def __init__(self, canonical_tool_name: str) -> None:
        self.canonical_tool_name = canonical_tool_name
        super().__init__(f"tool not in allowlist: {canonical_tool_name!r}")


class ValidationGateError(Exception):
    """Raised when a tool's output fails the output validation gate schema check."""


class BudgetWarningError(Exception):
    """Raised when an agent budget soft limit is approaching.

    Classifies as ISSUE / L3_HUMAN_ESCALATION so an operator can review before
    the hard ceiling is hit.
    """


class BudgetExhaustedError(Exception):
    """Raised when an agent budget hard ceiling is exceeded.

    Classifies as HARD_KILL / L4_SAFE_ABORT.
    """


class SLAViolationError(Exception):
    """Raised when an agent violates its configured SLA policy.

    Classifies as ISSUE tier so the host can decide whether to escalate.
    """


class CircuitBreakerStateUnavailableError(Exception):
    """Raised when the circuit breaker's backing state store (e.g. Redis) is unavailable.

    The circuit breaker fails closed rather than silently failing open.
    After the configured state_unavailable_retry_budget is exhausted, the SDK
    emits a rate-limited ISSUE / L3_HUMAN_ESCALATION rather than one alert per request.
    """
