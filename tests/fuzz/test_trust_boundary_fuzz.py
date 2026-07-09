"""Fuzz tests for trust-boundary redaction hardening."""

from __future__ import annotations

import base64
import json

from hypothesis import given, settings
from hypothesis import strategies as st

from agentic_exception_sdk.validation.trust_boundary import (
    REDACTED,
    SAFE_MESSAGE_MAX_CHARS,
    TrustBoundaryValidator,
)


@given(st.binary(max_size=2048))
@settings(max_examples=100)
def test_safe_exception_message_handles_random_bytes(data: bytes) -> None:
    validator = TrustBoundaryValidator()
    decoded = data.decode("utf-8", errors="surrogateescape")

    result = validator.safe_exception_message(ValueError(decoded))

    assert isinstance(result, str)
    assert len(result) <= SAFE_MESSAGE_MAX_CHARS


@given(st.binary(max_size=2048))
@settings(max_examples=100)
def test_safe_exception_message_handles_base64_wrapped_bytes(data: bytes) -> None:
    validator = TrustBoundaryValidator()
    wrapped = base64.b64encode(data).decode("ascii")

    result = validator.safe_exception_message(ValueError(wrapped))

    assert isinstance(result, str)
    assert len(result) <= SAFE_MESSAGE_MAX_CHARS


def test_safe_exception_message_fail_closed_on_json_decode_error() -> None:
    validator = TrustBoundaryValidator()

    result = validator.safe_exception_message(json.JSONDecodeError("raw secret", "", 0))

    assert result != "raw secret"
    assert isinstance(result, str)
    assert len(result) <= SAFE_MESSAGE_MAX_CHARS


def test_safe_exception_message_fail_closed_on_nested_json_decode_error() -> None:
    validator = TrustBoundaryValidator()
    parse_error = json.JSONDecodeError("raw secret", "", 0)
    outer = ValueError("outer wrapper")
    outer.__cause__ = parse_error

    result = validator.safe_exception_message(outer)

    assert result == REDACTED


def test_safe_exception_message_fail_closed_on_unicode_decode_error() -> None:
    validator = TrustBoundaryValidator()

    result = validator.safe_exception_message(
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "raw secret")
    )

    assert result == REDACTED


def test_safe_exception_message_fail_closed_on_nested_unicode_decode_error() -> None:
    validator = TrustBoundaryValidator()
    decode_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "raw secret")
    outer = ValueError("outer wrapper")
    outer.__context__ = decode_error

    result = validator.safe_exception_message(outer)

    assert result == REDACTED
