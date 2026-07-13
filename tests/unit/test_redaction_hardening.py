"""Redaction hardening from the pre-publication security audit (findings A/B/C).

- A: modern OpenAI project/scoped keys (sk-proj-..., sk-svcacct-...) must redact.
- B: Anthropic sk-ant-api03-... keys (with '_'/'-') must redact.
- C: context keys named Authorization/Bearer/Cookie/Session must be treated as
     sensitive — without re-introducing the over-redaction of ordinary words.
"""

from __future__ import annotations

import pytest

from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator

REDACTED = "[REDACTED]"


@pytest.fixture
def tb() -> TrustBoundaryValidator:
    return TrustBoundaryValidator()


# --- A & B: value-scanning catches current-format provider keys ---------------


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-Ab3dEfGhIjKlMnOpQrStUvWx1234567890",  # OpenAI project key
        "sk-svcacct-Ab3dEfGhIjKlMnOpQrStUvWx12345",  # OpenAI service-account key
        "sk-ant-api03-AbCd_EfGh-IjKlMnOpQrStUvWx12",  # Anthropic key ('_' and '-')
    ],
)
def test_modern_provider_keys_redacted_in_values(tb, secret):
    snap = tb.sanitize_context_snapshot({"note": f"the key is {secret}"})
    value = snap.root["note"]
    assert secret not in value
    assert REDACTED in value


# --- C: sensitive-named keys are redacted ------------------------------------


@pytest.mark.parametrize(
    "key",
    ["Authorization", "authorization", "bearer", "Cookie", "session_id", "sessionToken"],
)
def test_sensitive_named_keys_redacted(tb, key):
    snap = tb.sanitize_context_snapshot({key: "3f9c2d417ab54e6f8d2c9b1a0e7f6d5c"})
    assert snap.root[key] == REDACTED


# --- C guard: the fix must not re-introduce over-redaction of plain words -----


def test_ordinary_words_still_not_over_redacted(tb):
    snap = tb.sanitize_context_snapshot(
        {
            "author": "Jane Doe",
            "monkey_count": 3,
            "keyword": "sale",
            "session_duration_note": "ok",  # 'session' segment -> redacted (fail-safe)
        }
    )
    assert snap.root["author"] == "Jane Doe"
    assert snap.root["monkey_count"] == 3
    assert snap.root["keyword"] == "sale"
    # Documented, intentional tradeoff: 'session' is treated as sensitive.
    assert snap.root["session_duration_note"] == REDACTED
