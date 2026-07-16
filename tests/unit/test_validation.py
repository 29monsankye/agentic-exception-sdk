"""Tests for validation: trust boundary, guard rails, and output gates."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

from agentic_exception_sdk.taxonomy.envelope import SafeContextSnapshot
from agentic_exception_sdk.taxonomy.errors import GuardRailViolationError
from agentic_exception_sdk.validation.gates import NoOpGate, PydanticValidationGate
from agentic_exception_sdk.validation.guard_rails import (
    AllowlistedOperations,
    GuardRailPolicy,
    NoOpGuardRails,
)
from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator

# ---------------------------------------------------------------------------
# TrustBoundaryValidator: canonicalize_tool_name
# ---------------------------------------------------------------------------


def test_re2_available_flag_is_bool() -> None:
    from agentic_exception_sdk.validation.trust_boundary import RE2_AVAILABLE

    assert isinstance(RE2_AVAILABLE, bool)


def test_redaction_engine_module_name() -> None:
    from agentic_exception_sdk.validation import trust_boundary as tb

    assert tb._re_engine.__name__ in ("re2", "re")


def test_no_redaction_pattern_uses_re2_incompatible_u_escape() -> None:
    """Guard against the [re2] import crash regression (runs without re2 installed).

    google-re2 rejects raw ``\\uXXXX`` escapes ("invalid escape sequence: \\u"),
    which crashed the SDK at import under the recommended ``[re2]`` extra. Patterns
    must use literal code points (or ``\\x{...}``), never a ``\\u`` escape. The
    compiled pattern's source (``.pattern``) must therefore contain no ``\\u``.
    """
    from agentic_exception_sdk.validation.trust_boundary import _REDACTION_PATTERNS

    for pat, _ in _REDACTION_PATTERNS:
        assert "\\u" not in pat.pattern, (
            f"redaction pattern uses a \\u escape (rejected by google-re2): {pat.pattern!r}"
        )


def test_all_redaction_patterns_compile_under_re2() -> None:
    """When google-re2 is installed, every redaction pattern must compile under it.

    Exercises the recommended ``[re2]`` path that previously went untested. Skips
    cleanly when re2 is not available; a CI cell with ``[re2]`` should run it.
    """
    re2 = pytest.importorskip("re2")
    from agentic_exception_sdk.validation.trust_boundary import _REDACTION_PATTERNS

    for pat, _ in _REDACTION_PATTERNS:
        re2.compile(pat.pattern)  # must not raise

class TestCanonicalizeToolName:
    def setup_method(self):
        self.tb = TrustBoundaryValidator()

    def test_lowercases(self):
        assert self.tb.canonicalize_tool_name("SearchFlights") == "searchflights"

    def test_strips_whitespace(self):
        assert self.tb.canonicalize_tool_name("  search  ") == "search"

    def test_allows_underscores_hyphens_dots(self):
        assert self.tb.canonicalize_tool_name("search.flights-v2") == "search.flights-v2"

    def test_rejects_spaces(self):
        with pytest.raises(ValueError):
            self.tb.canonicalize_tool_name("has space")

    def test_rejects_colons(self):
        with pytest.raises(ValueError):
            self.tb.canonicalize_tool_name("has:colon")

    def test_rejects_empty_after_strip(self):
        with pytest.raises(ValueError):
            self.tb.canonicalize_tool_name("   ")

    def test_nfkc_normalization_applied(self):
        # Fullwidth characters normalize to ASCII
        result = self.tb.canonicalize_tool_name("ａｂｃ")
        assert result == "abc"

    def test_zero_width_chars_rejected(self):
        with pytest.raises(ValueError):
            self.tb.canonicalize_tool_name("abc​def")


# ---------------------------------------------------------------------------
# TrustBoundaryValidator: safe_exception_message
# ---------------------------------------------------------------------------

class TestSafeExceptionMessage:
    def setup_method(self):
        self.tb = TrustBoundaryValidator()

    def test_returns_string(self):
        result = self.tb.safe_exception_message(ValueError("test message"))
        assert isinstance(result, str)

    def test_truncates_to_500(self):
        long_exc = ValueError("x" * 1000)
        result = self.tb.safe_exception_message(long_exc)
        assert len(result) <= 500

    def test_redacts_bearer_token(self):
        exc = ValueError("Authorization: Bearer abc123defgh456ijklmn")
        result = self.tb.safe_exception_message(exc)
        assert "Bearer" not in result or "abc123" not in result

    def test_redacts_aws_key(self):
        exc = ValueError("key=AKIAIOSFODNN7EXAMPLE")
        result = self.tb.safe_exception_message(exc)
        assert "AKIA" not in result

    def test_redacts_password_assignment(self):
        exc = ValueError("password=supersecretpassword123")
        result = self.tb.safe_exception_message(exc)
        assert "supersecretpassword123" not in result

    def test_safe_message_passes_through(self):
        result = self.tb.safe_exception_message(TimeoutError("connection timed out after 30s"))
        assert "timed out" in result

    def test_empty_message(self):
        result = self.tb.safe_exception_message(ValueError(""))
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TrustBoundaryValidator: sanitize_context_snapshot
# ---------------------------------------------------------------------------

class TestSanitizeContextSnapshot:
    def setup_method(self):
        self.tb = TrustBoundaryValidator()

    def test_none_returns_empty_snapshot(self):
        snap = self.tb.sanitize_context_snapshot(None)
        assert isinstance(snap, SafeContextSnapshot)
        assert snap.root == {}

    def test_existing_snapshot_preserved(self):
        result = self.tb.sanitize_context_snapshot({"flight_id": "AA123"})
        assert result.root["flight_id"] == "AA123"

    def test_dict_input_converted(self):
        snap = self.tb.sanitize_context_snapshot({"x": 1, "y": "two"})
        assert snap.root["x"] == 1

    def test_sensitive_keys_redacted(self):
        snap = self.tb.sanitize_context_snapshot({"password": "secret123", "data": "ok"})
        assert snap.root.get("password") == "[REDACTED]"
        assert snap.root.get("data") == "ok"

    def test_unsupported_types_become_class_name(self):
        snap = self.tb.sanitize_context_snapshot({"obj": object()})
        assert snap.root["obj"] == "<object>"

    def test_deep_nesting_truncated(self):
        deep: dict = {"v": "leaf"}
        for _ in range(10):
            deep = {"nested": deep}
        snap = self.tb.sanitize_context_snapshot(deep)
        assert isinstance(snap, SafeContextSnapshot)

    def test_too_many_keys_truncated(self):
        data = {f"k{i}": i for i in range(200)}
        snap = self.tb.sanitize_context_snapshot(data)
        assert isinstance(snap, SafeContextSnapshot)
        assert len(snap.root) <= 100

    def test_secret_value_under_nonsensitive_key_redacted(self):
        # The key "note" is not sensitive, but an OpenAI-style key embedded in
        # its value must still be pattern-scanned and redacted.
        snap = self.tb.sanitize_context_snapshot(
            {"note": "the key is sk-abcdefghijklmnopqrstuvwxyz012345"}
        )
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in snap.root["note"]
        assert "[REDACTED]" in snap.root["note"]

    def test_aws_key_in_value_redacted(self):
        snap = self.tb.sanitize_context_snapshot({"log": "used AKIAIOSFODNN7EXAMPLE here"})
        assert "AKIAIOSFODNN7EXAMPLE" not in snap.root["log"]

    def test_bearer_token_in_nested_value_redacted(self):
        snap = self.tb.sanitize_context_snapshot(
            {"outer": {"header": "Bearer abcdefghijklmnopqrstuvwxyz0123456789"}}
        )
        assert "[REDACTED]" in snap.root["outer"]["header"]

    def test_secret_in_list_value_redacted(self):
        snap = self.tb.sanitize_context_snapshot(
            {"items": ["clean", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"]}
        )
        assert snap.root["items"][0] == "clean"
        assert "ghp_" not in snap.root["items"][1]

    def test_benign_value_passes_through_unchanged(self):
        snap = self.tb.sanitize_context_snapshot({"city": "London", "count": "three"})
        assert snap.root["city"] == "London"
        assert snap.root["count"] == "three"


# ---------------------------------------------------------------------------
# GuardRailPolicy tests
# ---------------------------------------------------------------------------

class TestGuardRailPolicy:
    def setup_method(self):
        self.tb = TrustBoundaryValidator()
        ops = AllowlistedOperations.from_iterable(
            ["search_flights", "book_hotel"],
            trust_boundary=self.tb,
        )
        self.policy = GuardRailPolicy(
            trust_boundary=self.tb,
            allowlisted_operations=ops,
        )

    def test_allowed_operation_passes(self):
        self.policy.check("search_flights")

    def test_disallowed_operation_raises(self):
        with pytest.raises(GuardRailViolationError):
            self.policy.check("delete_database")

    def test_case_insensitive_canonicalization(self):
        self.policy.check("SEARCH_FLIGHTS")

    def test_noop_guard_rails_allow_all(self):
        noop = NoOpGuardRails()
        noop.check("anything")  # Should not raise


# ---------------------------------------------------------------------------
# AllowlistedOperations tests
# ---------------------------------------------------------------------------

class TestAllowlistedOperations:
    def test_from_iterable_canonicalizes(self):
        tb = TrustBoundaryValidator()
        ops = AllowlistedOperations.from_iterable(
            ["SEARCH_FLIGHTS", "  Book_Hotel  "],
            trust_boundary=tb,
        )
        assert "search_flights" in ops.canonical
        assert "book_hotel" in ops.canonical


# ---------------------------------------------------------------------------
# OutputValidationGate tests
# ---------------------------------------------------------------------------

class TestOutputValidationGate:
    def test_noop_gate_passes_through(self):
        gate = NoOpGate()
        obj = object()
        assert gate.validate(obj) is obj

    def test_pydantic_gate_validates_correctly(self):
        class MyModel(BaseModel):
            value: int

        gate = PydanticValidationGate(MyModel)
        result = gate.validate({"value": 42})
        assert result.value == 42

    def test_pydantic_gate_raises_validation_gate_error(self):
        from agentic_exception_sdk.taxonomy.errors import ValidationGateError

        class MyModel(BaseModel):
            value: int

        gate = PydanticValidationGate(MyModel)
        with pytest.raises(ValidationGateError):
            gate.validate({"value": "not-an-int"})

    def test_validation_gate_error_hides_raw_input(self):
        from agentic_exception_sdk.taxonomy.errors import ValidationGateError

        class SecretModel(BaseModel):
            value: int

        gate = PydanticValidationGate(SecretModel)
        try:
            gate.validate({"value": "password=supersecret"})
        except ValidationGateError as exc:
            assert "password=supersecret" not in str(exc)


# ---------------------------------------------------------------------------
# Hypothesis: canonical tool names are stable
# ---------------------------------------------------------------------------

@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_.-", min_size=1, max_size=50))
@settings(max_examples=100)
def test_canonical_name_stable(name: str) -> None:
    tb = TrustBoundaryValidator()
    try:
        canon1 = tb.canonicalize_tool_name(name)
        canon2 = tb.canonicalize_tool_name(canon1)
        assert canon1 == canon2
    except ValueError:
        pass  # empty after strip — expected


def test_canonicalize_cache_returns_consistent_results() -> None:
    tb = TrustBoundaryValidator()
    assert tb.canonicalize_tool_name("Search_Flights") == "search_flights"
    assert tb.canonicalize_tool_name("Book_Hotel") == "book_hotel"
    assert tb.canonicalize_tool_name("Search_Flights") == "search_flights"  # cache hit
    assert tb.canonicalize_tool_name("Book_Hotel") == "book_hotel"  # cache hit


def test_redactor_fails_closed_on_pattern_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A crashing redaction pattern must fail closed, not leak partially-scanned text."""
    from agentic_exception_sdk.validation import trust_boundary

    class _BrokenPattern:
        def sub(self, replacement: str, string: str) -> str:
            raise RuntimeError("regex engine crash")

    monkeypatch.setattr(
        trust_boundary, "_REDACTION_PATTERNS", [(_BrokenPattern(), trust_boundary.REDACTED)]
    )
    tb = TrustBoundaryValidator()
    # A single pattern raising must yield the fully-redacted sentinel, never the raw text.
    result = tb.safe_exception_message(ValueError("secret=sk-abc123"))
    assert result == trust_boundary.REDACTED_BUDGET
