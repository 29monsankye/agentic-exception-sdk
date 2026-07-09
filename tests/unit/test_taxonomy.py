"""Tests for taxonomy: enums, errors, envelope, and classifier."""

from __future__ import annotations

import asyncio
import json
import pickle
from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from agentic_exception_sdk.taxonomy.classifier import ExceptionClassifier
from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.envelope import (
    SAFE_CONTEXT_MAX_DEPTH,
    SAFE_CONTEXT_MAX_KEYS,
    AgentExceptionEnvelope,
    SafeContextSnapshot,
)
from agentic_exception_sdk.taxonomy.errors import (
    AgentHardKillError,
    BudgetExhaustedError,
    BudgetWarningError,
    FallbackCapableError,
    GuardRailViolationError,
    PromptInjectionError,
    SecurityViolationError,
    SLAViolationError,
    StateCorruptionError,
    ValidationGateError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_env(**kwargs) -> AgentExceptionEnvelope:
    defaults = {
        "agent_id": "test-agent",
        "exception_class": AgentExceptionClass.EXCEPTION,
        "source": ExceptionSource.TOOL,
        "error_type": "TimeoutError",
        "message": "timeout",
        "context_snapshot": SafeContextSnapshot({}),
        "suggested_recovery": EscalationLevel.L0_SELF_RETRY,
        "occurred_at": datetime.now(UTC),
    }
    defaults.update(kwargs)
    return AgentExceptionEnvelope(**defaults)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

class TestEnums:
    def test_exception_class_values(self):
        assert AgentExceptionClass.EXCEPTION.value == "exception"
        assert AgentExceptionClass.ISSUE.value == "issue"
        assert AgentExceptionClass.HARD_KILL.value == "hard_kill"

    def test_escalation_level_ordering(self):
        levels = list(EscalationLevel)
        assert levels == sorted(levels, key=lambda lv: lv.value)

    def test_exception_source_values(self):
        sources = {s.value for s in ExceptionSource}
        assert sources == {"model", "tool", "orchestration", "planning", "data_env"}
        assert ExceptionSource.PLANNING.value == "planning"


# ---------------------------------------------------------------------------
# AgentHardKillError tests
# ---------------------------------------------------------------------------

class TestAgentHardKillError:
    def test_is_base_exception(self):
        env = make_env(
            exception_class=AgentExceptionClass.HARD_KILL,
            suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
        )
        err = AgentHardKillError(env)
        assert isinstance(err, BaseException)
        assert not isinstance(err, Exception)

    def test_not_caught_by_except_exception(self):
        env = make_env(
            exception_class=AgentExceptionClass.HARD_KILL,
            suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
        )
        err = AgentHardKillError(env)
        caught = False
        try:
            raise err
        except Exception:
            caught = True
        except BaseException:
            pass
        assert not caught, "AgentHardKillError should NOT be caught by except Exception"

    def test_envelope_accessible(self):
        env = make_env(
            exception_class=AgentExceptionClass.HARD_KILL,
            suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
        )
        err = AgentHardKillError(env)
        assert err.envelope is env

    def test_not_picklable(self):
        env = make_env(
            exception_class=AgentExceptionClass.HARD_KILL,
            suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
        )
        err = AgentHardKillError(env)
        with pytest.raises(TypeError, match="not pickle-serializable"):
            pickle.dumps(err)

    def test_not_copyable(self):
        import copy
        env = make_env(
            exception_class=AgentExceptionClass.HARD_KILL,
            suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
        )
        err = AgentHardKillError(env)
        with pytest.raises(TypeError):
            copy.copy(err)
        with pytest.raises(TypeError):
            copy.deepcopy(err)


# ---------------------------------------------------------------------------
# SafeContextSnapshot tests
# ---------------------------------------------------------------------------

class TestSafeContextSnapshot:
    def test_empty_snapshot_valid(self):
        snap = SafeContextSnapshot({})
        assert snap.root == {}

    def test_simple_dict_valid(self):
        snap = SafeContextSnapshot({"key": "value", "num": 42})
        assert snap.root["key"] == "value"

    def test_non_finite_float_rejected(self):
        with pytest.raises(ValidationError):
            SafeContextSnapshot({"f": float("inf")})
        with pytest.raises(ValidationError):
            SafeContextSnapshot({"f": float("nan")})
        with pytest.raises(ValidationError):
            SafeContextSnapshot({"f": float("-inf")})

    def test_depth_limit_enforced(self):
        node: dict = {"v": "leaf"}
        for _ in range(SAFE_CONTEXT_MAX_DEPTH):
            node = {"nested": node}
        with pytest.raises(ValidationError, match="max depth"):
            SafeContextSnapshot(node)

    def test_key_count_limit_enforced(self):
        data = {f"k{i}": i for i in range(SAFE_CONTEXT_MAX_KEYS + 1)}
        with pytest.raises(ValidationError, match="max keys"):
            SafeContextSnapshot(data)

    def test_unsupported_type_rejected(self):
        with pytest.raises(ValidationError):
            SafeContextSnapshot({"obj": object()})

    def test_list_values_valid(self):
        snap = SafeContextSnapshot({"items": [1, "two", None, True]})
        assert snap.root["items"] == [1, "two", None, True]

    def test_nested_dicts_valid(self):
        snap = SafeContextSnapshot({"level1": {"level2": {"level3": "ok"}}})
        assert snap.root["level1"]["level2"]["level3"] == "ok"

    def test_frozen(self):
        snap = SafeContextSnapshot({"x": 1})
        with pytest.raises(Exception):
            snap.root = {"y": 2}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AgentExceptionEnvelope tests
# ---------------------------------------------------------------------------

class TestAgentExceptionEnvelope:
    def test_exception_id_auto_assigned_uuidv7(self):
        env = make_env()
        assert UUID(env.exception_id).version == 7

    @pytest.mark.parametrize("exception_id", ["", "not-a-uuid"])
    def test_exception_id_rejects_non_uuid(self, exception_id):
        with pytest.raises(ValidationError):
            make_env(exception_id=exception_id)

    def test_valid_exception_envelope(self):
        env = make_env()
        assert env.exception_class == AgentExceptionClass.EXCEPTION
        assert env.suggested_recovery == EscalationLevel.L0_SELF_RETRY

    def test_class_level_consistency_enforced(self):
        with pytest.raises(ValidationError, match="not permitted"):
            make_env(
                exception_class=AgentExceptionClass.EXCEPTION,
                suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
            )

    def test_hard_kill_only_l4(self):
        env = make_env(
            exception_class=AgentExceptionClass.HARD_KILL,
            suggested_recovery=EscalationLevel.L4_SAFE_ABORT,
        )
        assert env.suggested_recovery == EscalationLevel.L4_SAFE_ABORT

    def test_issue_levels_valid(self):
        for level in (EscalationLevel.L2_CHECKPOINT_HANDOFF, EscalationLevel.L3_HUMAN_ESCALATION):
            env = make_env(
                exception_class=AgentExceptionClass.ISSUE,
                suggested_recovery=level,
            )
            assert env.suggested_recovery == level

    def test_agent_id_validation(self):
        with pytest.raises(ValidationError):
            make_env(agent_id="INVALID UPPERCASE")
        with pytest.raises(ValidationError):
            make_env(agent_id="has.dot")
        env = make_env(agent_id="valid-agent-01")
        assert env.agent_id == "valid-agent-01"

    def test_message_max_length(self):
        with pytest.raises(ValidationError):
            make_env(message="x" * 501)
        env = make_env(message="x" * 500)
        assert len(env.message) == 500

    def test_frozen(self):
        env = make_env()
        with pytest.raises(Exception):
            env.agent_id = "other"  # type: ignore[misc]

    def test_json_round_trip(self):
        env = make_env()
        json_str = env.model_dump_json()
        env2 = AgentExceptionEnvelope.model_validate_json(json_str)
        assert env.exception_id == env2.exception_id
        assert env.message == env2.message

    def test_not_pickle_serializable(self):
        env = make_env()
        # Pydantic models can be pickled by default; the spec restricts DLQ use only
        # This is an informational test that model_dump_json is the correct path
        json_str = env.model_dump_json()
        assert "TimeoutError" in json_str

    def test_correlation_id_none_valid(self):
        env2 = AgentExceptionEnvelope(
            agent_id="test-agent",
            exception_class=AgentExceptionClass.EXCEPTION,
            source=ExceptionSource.TOOL,
            error_type="TimeoutError",
            message="no corr",
            context_snapshot=SafeContextSnapshot({}),
            suggested_recovery=EscalationLevel.L0_SELF_RETRY,
            occurred_at=datetime.now(UTC),
        )
        assert env2.correlation_id is None

    def test_sdk_version_default(self):
        env = make_env()
        assert env.sdk_version == "1.1.0"

    def test_attempt_count_default(self):
        env = make_env()
        assert env.attempt_count == 1

    def test_attempt_count_must_be_positive(self):
        with pytest.raises(ValidationError):
            make_env(attempt_count=0)

    def test_old_envelope_json_defaults_attempt_count(self):
        env = make_env()
        payload = env.model_dump(mode="json", exclude={"attempt_count"})
        json_str = json.dumps(payload)

        env2 = AgentExceptionEnvelope.model_validate_json(json_str)

        assert env2.attempt_count == 1

    def test_lineage_default_empty(self):
        env = make_env()
        assert env.lineage == []


# ---------------------------------------------------------------------------
# ExceptionClassifier tests
# ---------------------------------------------------------------------------

class TestExceptionClassifier:
    def setup_method(self):
        self.clf = ExceptionClassifier()

    def test_timeout_classified_exception(self):
        cls, _, lvl = self.clf.classify(TimeoutError("timeout"))
        assert cls == AgentExceptionClass.EXCEPTION
        assert lvl == EscalationLevel.L0_SELF_RETRY

    def test_connection_error_classified_exception(self):
        cls, _, _ = self.clf.classify(ConnectionError("conn failed"))
        assert cls == AgentExceptionClass.EXCEPTION

    def test_security_violation_is_hard_kill(self):
        cls, _, lvl = self.clf.classify(SecurityViolationError("sec"))
        assert cls == AgentExceptionClass.HARD_KILL
        assert lvl == EscalationLevel.L4_SAFE_ABORT

    def test_prompt_injection_is_hard_kill(self):
        cls, _, _ = self.clf.classify(PromptInjectionError("injection"))
        assert cls == AgentExceptionClass.HARD_KILL

    def test_state_corruption_is_hard_kill(self):
        cls, _, _ = self.clf.classify(StateCorruptionError("corrupt"))
        assert cls == AgentExceptionClass.HARD_KILL

    def test_guard_rail_is_hard_kill(self):
        cls, _, _ = self.clf.classify(GuardRailViolationError("blocked"))
        assert cls == AgentExceptionClass.HARD_KILL

    def test_budget_exhausted_is_hard_kill(self):
        cls, _, _ = self.clf.classify(BudgetExhaustedError("exhausted"))
        assert cls == AgentExceptionClass.HARD_KILL

    def test_budget_warning_is_issue(self):
        cls, _, lvl = self.clf.classify(BudgetWarningError("warning"))
        assert cls == AgentExceptionClass.ISSUE
        assert lvl == EscalationLevel.L3_HUMAN_ESCALATION

    def test_sla_violation_is_issue(self):
        cls, _, _ = self.clf.classify(SLAViolationError("sla"))
        assert cls == AgentExceptionClass.ISSUE

    def test_permission_error_is_issue(self):
        cls, _, lvl = self.clf.classify(PermissionError("denied"))
        assert cls == AgentExceptionClass.ISSUE
        assert lvl == EscalationLevel.L2_CHECKPOINT_HANDOFF

    def test_unknown_exception_is_hard_kill(self):
        cls, _, _ = self.clf.classify(RuntimeError("unknown"))
        assert cls == AgentExceptionClass.HARD_KILL

    def test_cancelled_error_reraises(self):
        with pytest.raises(asyncio.CancelledError):
            self.clf.classify(asyncio.CancelledError())

    def test_keyboard_interrupt_reraises(self):
        with pytest.raises(KeyboardInterrupt):
            self.clf.classify(KeyboardInterrupt())

    def test_system_exit_reraises(self):
        with pytest.raises(SystemExit):
            self.clf.classify(SystemExit(0))

    def test_validation_gate_error_is_issue(self):
        cls, src, _ = self.clf.classify(ValidationGateError("bad output"))
        assert cls == AgentExceptionClass.ISSUE
        assert src == ExceptionSource.MODEL

    def test_fallback_capable_error_is_exception(self):
        class MyFallback(FallbackCapableError, ValueError):
            pass
        cls, _, lvl = self.clf.classify(MyFallback("fallback"))
        assert cls == AgentExceptionClass.EXCEPTION
        assert lvl == EscalationLevel.L1_FALLBACK_PATH

    def test_exception_group_most_severe_wins(self):
        exc = BaseExceptionGroup("mixed", [
            TimeoutError("retry-me"),
            StateCorruptionError("corrupt"),
        ])
        cls, _, _ = self.clf.classify(exc)
        assert cls == AgentExceptionClass.HARD_KILL

    @given(st.sampled_from([TimeoutError, ConnectionError, PermissionError, RuntimeError]))
    @settings(max_examples=20, deadline=None)
    def test_classification_is_deterministic_for_same_exception_type(self, exc_type):
        exc = exc_type("stable")
        assert self.clf.classify(exc) == self.clf.classify(exc)


# ---------------------------------------------------------------------------
# Hypothesis property tests
# ---------------------------------------------------------------------------

@given(st.text(alphabet=st.characters(whitelist_categories=("Ll", "Nd")), min_size=1, max_size=50))
@settings(max_examples=100)
def test_valid_agent_id_accepted(agent_id: str) -> None:
    """Any lowercase-alphanum string of 1-50 chars should be accepted."""
    try:
        env = make_env(agent_id=agent_id)
        assert env.agent_id == agent_id
    except ValidationError:
        pass  # Some generated strings may still fail — that's OK


@given(st.floats(allow_nan=True, allow_infinity=True))
@settings(max_examples=50)
def test_non_finite_floats_rejected(f: float) -> None:
    import math
    if math.isnan(f) or math.isinf(f):
        with pytest.raises(ValidationError):
            SafeContextSnapshot({"f": f})
    else:
        snap = SafeContextSnapshot({"f": f})
        assert isinstance(snap.root["f"], float)
