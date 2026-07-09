from __future__ import annotations

from agentic_exception_sdk.taxonomy.errors import (
    BudgetExhaustedError,
    GuardRailViolationError,
    PromptInjectionError,
    SecurityViolationError,
)
from agentic_exception_sdk.taxonomy.owasp import OWASPLLMRisk, classify_owasp


def test_prompt_injection_maps_to_llm01() -> None:
    assert (
        classify_owasp(PromptInjectionError("prompt injection"))
        is OWASPLLMRisk.LLM01_PROMPT_INJECTION
    )


def test_budget_exhausted_maps_to_llm10() -> None:
    assert (
        classify_owasp(BudgetExhaustedError("budget exhausted"))
        is OWASPLLMRisk.LLM10_UNBOUNDED_CONSUMPTION
    )


def test_unknown_exception_returns_none() -> None:
    assert classify_owasp(RuntimeError("unknown")) is None


def test_all_enum_values_are_owasp_strings() -> None:
    assert all(risk.value.startswith(("LLM0", "LLM1")) for risk in OWASPLLMRisk)


def test_guardrail_violation_maps_to_llm06_not_llm01() -> None:
    # GuardRailViolationError IS-A SecurityViolationError; must resolve to LLM06
    # (excessive agency), not LLM01 (prompt injection via parent class match).
    assert (
        classify_owasp(GuardRailViolationError("guardrail hit"))
        is OWASPLLMRisk.LLM06_EXCESSIVE_AGENCY
    )


def test_security_violation_base_maps_to_llm01() -> None:
    # The parent class itself (not a subclass) should still map to LLM01.
    assert (
        classify_owasp(SecurityViolationError("generic security violation"))
        is OWASPLLMRisk.LLM01_PROMPT_INJECTION
    )
