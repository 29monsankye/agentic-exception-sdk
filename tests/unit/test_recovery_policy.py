from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_exception_sdk import (
    AgentExceptionClass,
    AgentExceptionEnvelope,
    AgentHardKillError,
    EscalationLevel,
    ExceptionSource,
    PromptInjectionError,
    RecoveryDirective,
    ResilienceBundle,
    SafeContextSnapshot,
    resilient,
)
from agentic_exception_sdk.resilience.recovery import SuggestedRecoveryPolicy


class _NonePolicy:
    def __init__(self) -> None:
        self.calls = 0

    def recover(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None:
        self.calls += 1
        return None


class _ResumePolicy:
    def recover(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None:
        return RecoveryDirective(action="resume", resume_state={"ok": True})


def _envelope(level: EscalationLevel) -> AgentExceptionEnvelope:
    if level <= EscalationLevel.L1_FALLBACK_PATH:
        exc_class = AgentExceptionClass.EXCEPTION
    elif level <= EscalationLevel.L3_HUMAN_ESCALATION:
        exc_class = AgentExceptionClass.ISSUE
    else:
        exc_class = AgentExceptionClass.HARD_KILL
    return AgentExceptionEnvelope(
        exception_id="00000000-0000-0000-0000-000000000001",
        agent_id="agent-a",
        tool_name="tool-a",
        exception_class=exc_class,
        source=ExceptionSource.TOOL,
        error_type="TimeoutError",
        message="timeout",
        context_snapshot=SafeContextSnapshot({}),
        suggested_recovery=level,
        occurred_at=datetime.now(UTC),
        correlation_id="corr-a",
        lineage=["agent-a"],
    )


def test_recovery_policy_none_falls_through_to_existing_defaults() -> None:
    policy = _NonePolicy()
    bundle = ResilienceBundle(recovery_policy=policy)

    with pytest.raises(TimeoutError):
        resilient(bundle, tool_name="tool-a", agent_id="agent-a")(
            lambda: (_raise_timeout())
        )()

    assert policy.calls == 1


def test_recovery_policy_resume_returns_resume_state() -> None:
    bundle = ResilienceBundle(recovery_policy=_ResumePolicy())

    result = resilient(bundle, tool_name="tool-a", agent_id="agent-a")(
        lambda: (_raise_timeout())
    )()

    assert result == {"ok": True}


def test_recovery_policy_not_called_for_hard_kill() -> None:
    policy = _NonePolicy()
    bundle = ResilienceBundle(recovery_policy=policy)

    with pytest.raises(AgentHardKillError):
        resilient(bundle, tool_name="tool-a", agent_id="agent-a")(
            lambda: (_raise_prompt_injection())
        )()

    assert policy.calls == 0


def test_suggested_recovery_policy_routes_by_recovery_level() -> None:
    policy = SuggestedRecoveryPolicy()

    assert policy.recover(_envelope(EscalationLevel.L0_SELF_RETRY)) is None

    l1 = policy.recover(_envelope(EscalationLevel.L1_FALLBACK_PATH))
    assert l1 == RecoveryDirective(action="resume", resume_state=None)

    l2 = policy.recover(_envelope(EscalationLevel.L2_CHECKPOINT_HANDOFF))
    assert l2 == RecoveryDirective(action="escalate")


def _raise_timeout() -> None:
    raise TimeoutError("upstream unavailable")


def _raise_prompt_injection() -> None:
    raise PromptInjectionError("prompt injection detected")
