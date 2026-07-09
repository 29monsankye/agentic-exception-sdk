"""Agent budget data models.

Monetary ceilings use integer micro-USD (1 USD == 1_000_000 micro-USD).
Binary floating point is never used for monetary values.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AgentBudget", "UnlimitedBudget"]


@dataclass(frozen=True)
class AgentBudget:
    """Resource ceiling configuration for an agent run.

    All fields are optional. When None, that ceiling is not enforced.
    Non-LLM workloads can ignore token/cost fields entirely; LLM-facing
    systems should set all relevant ceilings.

    Monetary ceilings use integer micro-USD where $1.00 == 1_000_000.
    Never use binary floating point for monetary ceilings.

    Attributes:
        max_seconds: Wall-clock time limit in seconds for the entire agent run.
        max_tool_calls: Maximum total tool call invocations allowed.
        failure_budget: Maximum number of failed tool call attempts before escalation.
        max_input_tokens: Maximum input tokens consumed across all LLM calls.
        max_output_tokens: Maximum output tokens generated across all LLM calls.
        max_total_cost_micros_usd: Maximum total cost in integer micro-USD.
    """

    max_seconds: float | None = None
    max_tool_calls: int | None = None
    failure_budget: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_total_cost_micros_usd: int | None = None


def UnlimitedBudget() -> AgentBudget:
    """Return an AgentBudget with no enforced limits.

    Returns:
        An AgentBudget where all ceiling fields are None.
    """
    return AgentBudget()
