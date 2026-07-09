"""Taxonomy: enums, errors, envelope, and classifier."""

from __future__ import annotations

from agentic_exception_sdk.taxonomy.classifier import ExceptionClassifier
from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope, SafeContextSnapshot
from agentic_exception_sdk.taxonomy.errors import (
    AgentHardKillError,
    BudgetExhaustedError,
    BudgetWarningError,
    CircuitBreakerStateUnavailableError,
    CompensationPartialFailureError,
    FallbackCapableError,
    GuardRailViolationError,
    PromptInjectionError,
    SecurityViolationError,
    SLAViolationError,
    StateCorruptionError,
    ToolKindMismatchError,
    ValidationGateError,
)
from agentic_exception_sdk.taxonomy.owasp import (
    EXCEPTION_OWASP_MAP,
    OWASPLLMRisk,
    classify_owasp,
)

__all__ = [
    "AgentExceptionClass",
    "AgentExceptionEnvelope",
    "AgentHardKillError",
    "BudgetExhaustedError",
    "BudgetWarningError",
    "CircuitBreakerStateUnavailableError",
    "CompensationPartialFailureError",
    "EscalationLevel",
    "EXCEPTION_OWASP_MAP",
    "ExceptionClassifier",
    "ExceptionSource",
    "FallbackCapableError",
    "GuardRailViolationError",
    "OWASPLLMRisk",
    "PromptInjectionError",
    "SLAViolationError",
    "SafeContextSnapshot",
    "SecurityViolationError",
    "StateCorruptionError",
    "ToolKindMismatchError",
    "ValidationGateError",
    "classify_owasp",
]
