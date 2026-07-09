"""OWASP LLM Top 10 (2025) risk identifiers.

Used to tag AgentExceptionEnvelope records with the OWASP vulnerability class
they represent, enabling compliance reporting and audit trail cross-referencing.
"""

from __future__ import annotations

from enum import StrEnum

from agentic_exception_sdk.taxonomy.errors import (
    BudgetExhaustedError,
    BudgetWarningError,
    GuardRailViolationError,
    PromptInjectionError,
    SecurityViolationError,
    ValidationGateError,
)

__all__ = ["EXCEPTION_OWASP_MAP", "OWASPLLMRisk", "classify_owasp"]


class OWASPLLMRisk(StrEnum):
    """OWASP LLM Top 10 (2025) risk identifiers."""

    LLM01_PROMPT_INJECTION = "LLM01"
    LLM02_SENSITIVE_DISCLOSURE = "LLM02"
    LLM03_SUPPLY_CHAIN = "LLM03"
    LLM04_DATA_POISONING = "LLM04"
    LLM05_IMPROPER_OUTPUT = "LLM05"
    LLM06_EXCESSIVE_AGENCY = "LLM06"
    LLM07_SYSTEM_PROMPT_LEAKAGE = "LLM07"
    LLM08_VECTOR_WEAKNESSES = "LLM08"
    LLM09_MISINFORMATION = "LLM09"
    LLM10_UNBOUNDED_CONSUMPTION = "LLM10"


EXCEPTION_OWASP_MAP: dict[type[BaseException], OWASPLLMRisk] = {
    # More-specific subclasses must precede their parents so classify_owasp()
    # iterates in MRO order and returns the tightest match first.
    PromptInjectionError: OWASPLLMRisk.LLM01_PROMPT_INJECTION,
    GuardRailViolationError: OWASPLLMRisk.LLM06_EXCESSIVE_AGENCY,
    SecurityViolationError: OWASPLLMRisk.LLM01_PROMPT_INJECTION,
    BudgetExhaustedError: OWASPLLMRisk.LLM10_UNBOUNDED_CONSUMPTION,
    BudgetWarningError: OWASPLLMRisk.LLM10_UNBOUNDED_CONSUMPTION,
    ValidationGateError: OWASPLLMRisk.LLM05_IMPROPER_OUTPUT,
}


def classify_owasp(exc: BaseException) -> OWASPLLMRisk | None:
    """Return the OWASP risk category for a known SDK exception, or None."""
    for exc_type, risk in EXCEPTION_OWASP_MAP.items():
        if isinstance(exc, exc_type):
            return risk
    return None
