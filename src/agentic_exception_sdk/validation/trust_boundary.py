"""Trust boundary validator — sanitizes all host-provided strings and snapshots.

All host-provided strings and snapshots that enter an envelope are untrusted.
They must be normalized and sanitized before the envelope is constructed. This
includes string *values* inside context snapshots: they are pattern-scanned with
the same redaction rules as exception messages, not merely length-bounded.

ReDoS protection: inputs are truncated before any regex is applied. All patterns
are precompiled at class level with no nested quantifiers. A 10ms execution
budget is enforced; if exceeded, exactly "[REDACTED:budget_exceeded]" is returned.

Production redaction uses google-re2 when available for linear-time matching.
Built-in re fallback patterns avoid nested quantifiers and ambiguous .* constructs.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from agentic_exception_sdk.taxonomy.envelope import SafeContextSnapshot

_log = logging.getLogger(__name__)

SAFE_MESSAGE_MAX_CHARS: int = 500
SAFE_IDENTIFIER_MAX_CHARS: int = 256
SAFE_CONTEXT_MAX_DEPTH: int = 5
SAFE_CONTEXT_MAX_KEYS: int = 100
REDACTED: str = "[REDACTED]"
REDACTED_BUDGET: str = "[REDACTED:budget_exceeded]"
TRUNCATED: str = "[TRUNCATED]"
REDACTION_BUDGET_MS: float = 10.0

# Attempt to import google-re2 for linear-time regex matching
try:
    import re2 as _re_engine
except ImportError:
    _re_engine = re
    _RE2_AVAILABLE = False
    _log.warning(
        "google-re2 not installed; redaction falls back to stdlib re. "
        "Install agentic-exception-sdk[re2] for linear-time ReDoS protection."
    )
else:
    _RE2_AVAILABLE = True

RE2_AVAILABLE: bool = _RE2_AVAILABLE

# Sensitive context key patterns for snapshot redaction
_SENSITIVE_KEY_RE: re.Pattern[str] = re.compile(
    r"(?i).*(password|passwd|token|secret|key|credential|auth|api_?key).*"
)

# Precompiled ReDoS-resistant redaction patterns.
# All patterns avoid nested quantifiers and ambiguous .* constructs.
_REDACTION_PATTERNS: list[tuple[Any, str]] = [
    # password/token/secret/credential assignments (key=value style)
    (
        _re_engine.compile(
            r"(?i)(password|passwd|secret|token|credential|api.?key|apikey|auth)[\"'\s]*[=:][\"'\s]*\S{1,200}"
        ),
        REDACTED,
    ),
    # URL query secrets: ?key=val or &key=val
    (
        _re_engine.compile(
            r"(?i)[?&](api.?key|token|secret|key|password|auth|credential)[=][^\s&]{1,200}"
        ),
        REDACTED,
    ),
    # Email addresses
    (
        _re_engine.compile(r"\b[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,63}\b"),
        REDACTED,
    ),
    # Bearer tokens
    (_re_engine.compile(r"(?i)bearer\s+[a-zA-Z0-9\-._~+/]{10,500}=*"), REDACTED),
    # AWS access keys: AKIA followed by 16 uppercase alphanumeric
    (_re_engine.compile(r"AKIA[0-9A-Z]{16}"), REDACTED),
    # GitHub PATs
    (_re_engine.compile(r"(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{20,255}"), REDACTED),
    (_re_engine.compile(r"github_pat_[a-zA-Z0-9_]{20,255}"), REDACTED),
    # OpenAI key
    (_re_engine.compile(r"sk-[a-zA-Z0-9]{20,255}"), REDACTED),
    # Anthropic key
    (_re_engine.compile(r"sk-ant-[a-zA-Z0-9\-]{20,255}"), REDACTED),
    # JWTs: base64url.base64url.base64url
    (
        _re_engine.compile(
            r"eyJ[a-zA-Z0-9_\-]{10,500}\.eyJ[a-zA-Z0-9_\-]{10,500}\.[a-zA-Z0-9_\-]{10,500}"
        ),
        REDACTED,
    ),
    # Credit card PANs: 13-19 digit sequences with optional separators
    (_re_engine.compile(r"\b(?:\d[ \-]?){13,19}\b"), REDACTED),
    # Unicode bidi override / zero-width characters.
    # Literal code points via non-raw \u escapes (Python decodes them at parse time).
    # google-re2 rejects raw \uXXXX escapes ("invalid escape sequence: \u"), which
    # crashed at import under the [re2] extra; literal chars compile under both engines.
    (_re_engine.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]"), REDACTED),
]

__all__ = ["RE2_AVAILABLE", "TrustBoundaryValidator"]


class TrustBoundaryValidator:
    """Sanitizes exception text and host context snapshots before SDK propagation.

    This is a core security boundary. No host-provided string enters an envelope
    without passing through this validator. Sanitizer failures must not mask the
    original exception — all methods are written defensively.

    Args:
        metrics_collector: Optional metrics collector for incrementing counters.
                           If None, metric increments are silently skipped.
    """

    def __init__(self, metrics_collector: Any | None = None) -> None:
        self._metrics = metrics_collector
        self._tool_name_cache: dict[str, str] = {}

    def _incr(self, counter_name: str) -> None:
        """Increment a named counter on the metrics collector if available."""
        if self._metrics is not None:
            with suppress(Exception):
                getattr(self._metrics, counter_name, lambda: None)()

    def canonicalize_tool_name(self, tool_name: str) -> str:
        """Return NFKC-normalized, stripped, lowercased, validated tool name.

        Validation requires the name to match ``[a-z0-9_.-]+`` after normalization.
        Control characters, whitespace separators, zero-width characters, bidi
        controls, and Unicode homoglyph tricks are rejected.

        Both the raw and canonical forms are preserved at the trust boundary for
        diagnostics. If canonicalization changes the name,
        tool_name_canonicalization_modified_total is incremented.

        Results are cached per-instance: tool names are stable across calls so
        the Unicode normalization and regex run only once per unique raw name.

        Args:
            tool_name: The raw tool name from the host.

        Returns:
            The canonical tool name.

        Raises:
            ValueError: If the normalized name contains disallowed characters.
        """
        cached = self._tool_name_cache.get(tool_name)
        if cached is not None:
            return cached
        normalized = unicodedata.normalize("NFKC", tool_name).strip().lower()
        if not re.fullmatch(r"^[a-z0-9_.\-]+$", normalized):
            raise ValueError(
                f"tool name {tool_name!r} contains disallowed characters after NFKC normalization"
            )
        if normalized != tool_name:
            self._incr("tool_name_canonicalization_modified_total")
        self._tool_name_cache[tool_name] = normalized
        return normalized

    def safe_exception_message(self, exc: BaseException) -> str:
        """Return bounded, single-line, redacted exception text.

        This method never returns raw str(exc) directly. It:
        1. Truncates to SAFE_MESSAGE_MAX_CHARS before any regex (ReDoS protection).
        2. Strips Cc/Cf/Cs Unicode categories (control, format, surrogate chars).
        3. Applies precompiled ReDoS-resistant redaction patterns.
        4. Enforces a 10ms budget; returns "[REDACTED:budget_exceeded]" if exceeded.

        Args:
            exc: The caught exception to extract a message from.

        Returns:
            A sanitized, bounded string safe for logging and envelope storage.
        """
        if self._chain_contains_parse_error(exc):
            return REDACTED
        try:
            raw = self._exception_chain_text(exc)[:SAFE_MESSAGE_MAX_CHARS]
        except Exception:
            return REDACTED

        # Strip control/format/surrogate Unicode categories
        cleaned = "".join(ch for ch in raw if unicodedata.category(ch) not in ("Cc", "Cf", "Cs"))

        redacted = self._apply_redaction_patterns(cleaned, time.monotonic())
        if redacted is None:
            return REDACTED_BUDGET

        self._incr("sanitizer_redaction_total")
        return redacted[:SAFE_MESSAGE_MAX_CHARS]

    def _apply_redaction_patterns(self, text: str, start: float) -> str | None:
        """Apply the precompiled redaction patterns under the shared ReDoS budget.

        The caller passes ``start`` (a ``time.monotonic()`` reading) so a single
        budget can span an entire operation — one exception message, or a whole
        context-snapshot walk across many string values. The budget is checked
        before each pattern; once REDACTION_BUDGET_MS is exceeded the scan stops
        and the method fails closed.

        Args:
            text: The (already length-bounded) string to scan.
            start: The monotonic clock reading the budget is measured from.

        Returns:
            The redacted string, or ``None`` if the budget was exceeded — in which
            case the caller must substitute REDACTED_BUDGET (fail closed) rather
            than return partially-scanned text.
        """
        result = text
        for pattern, replacement in _REDACTION_PATTERNS:
            elapsed_ms = (time.monotonic() - start) * 1000
            if elapsed_ms > REDACTION_BUDGET_MS:
                self._incr("sanitizer_redaction_timeout_total")
                return None
            with suppress(Exception):
                result = pattern.sub(replacement, result)
        return result

    def safe_identifier(self, value: object) -> str:
        """Return a redacted, bounded string for span/log identifier attributes.

        Identifiers are host-controlled too. They may carry secrets, control
        characters, or intentionally oversized values, so they share the message
        sanitizer and then receive a stricter 256-character cap. Overlong values
        end with ``[TRUNCATED]``.
        """
        try:
            result = self.safe_exception_message(ValueError(str(value)))
        except Exception:
            return REDACTED
        if len(result) <= SAFE_IDENTIFIER_MAX_CHARS:
            return result
        keep = SAFE_IDENTIFIER_MAX_CHARS - len(TRUNCATED)
        return f"{result[:keep]}{TRUNCATED}"

    def _chain_contains_parse_error(self, exc: BaseException) -> bool:
        """Return True if any exception in the cause/context chain is parse-related."""
        seen: set[int] = set()
        stack: list[BaseException] = [exc]

        while stack:
            current = stack.pop()
            current_id = id(current)
            if current_id in seen:
                continue
            seen.add(current_id)
            if isinstance(current, json.JSONDecodeError | UnicodeDecodeError):
                return True
            if current.__context__ is not None and not current.__suppress_context__:
                stack.append(current.__context__)
            if current.__cause__ is not None:
                stack.append(current.__cause__)
        return False

    def _exception_chain_text(self, exc: BaseException) -> str:
        """Return bounded text from exc plus cause/context without following cycles."""
        parts: list[str] = []
        seen: set[int] = set()
        stack: list[BaseException] = [exc]

        while stack and len(" | ".join(parts)) < SAFE_MESSAGE_MAX_CHARS:
            current = stack.pop()
            current_id = id(current)
            if current_id in seen:
                continue
            seen.add(current_id)

            with suppress(Exception):
                text = str(current)
                if text:
                    parts.append(text)

            cause = current.__cause__
            context = current.__context__
            if context is not None and not current.__suppress_context__:
                stack.append(context)
            if cause is not None:
                stack.append(cause)

        return " | ".join(parts)

    def sanitize_context_snapshot(
        self,
        context_snapshot: Mapping[str, Any] | None,
    ) -> SafeContextSnapshot:
        """Return a bounded, serializable, redacted copy of host state.

        The sanitizer:
        - Caps dict nesting at SAFE_CONTEXT_MAX_DEPTH levels.
        - Caps keys per dict to SAFE_CONTEXT_MAX_KEYS.
        - Redacts values for sensitive keys (password, token, secret, etc.).
        - Pattern-scans every string value (under any key) with the same
          redaction patterns used for exception messages, so secrets embedded in
          otherwise-benign fields (``{"note": "token is sk-..."}``) are redacted.
        - Replaces unsupported types (datetime, Decimal, UUID, bytes, custom classes)
          with ``"<ClassName>"`` strings and increments sanitizer_unsupported_type_total.
        - Replaces non-finite floats with ``"<non-finite-float>"``.
        - Never raises — sanitizer failures must not mask the original exception.

        A single ReDoS budget (REDACTION_BUDGET_MS) spans the whole walk. If it is
        exhausted, remaining string values fail closed to REDACTED_BUDGET rather
        than passing through unscanned.

        Args:
            context_snapshot: Host-provided state mapping or None.

        Returns:
            A SafeContextSnapshot containing only JSON-safe, bounded, redacted values.
        """
        if context_snapshot is None:
            return SafeContextSnapshot({})
        try:
            sanitized = self._sanitize_node(context_snapshot, depth=0, start=time.monotonic())
            return SafeContextSnapshot(sanitized)
        except Exception:
            return SafeContextSnapshot({})

    def _sanitize_node(self, node: Any, depth: int, start: float) -> Any:
        """Recursively sanitize a context node.

        Args:
            node: Any value from the host context.
            depth: Current recursion depth.
            start: Monotonic clock reading the shared redaction budget is measured
                from; threaded unchanged through the whole snapshot walk.

        Returns:
            A JSON-safe sanitized value.
        """
        if depth > SAFE_CONTEXT_MAX_DEPTH:
            return "<depth-limit>"

        if isinstance(node, dict):
            result: dict[str, Any] = {}
            items = list(node.items())[:SAFE_CONTEXT_MAX_KEYS]
            for k, v in items:
                key_str = str(k) if not isinstance(k, str) else k
                if _SENSITIVE_KEY_RE.match(key_str):
                    result[key_str] = REDACTED
                else:
                    result[key_str] = self._sanitize_node(v, depth + 1, start)
            return result

        if isinstance(node, list):
            return [self._sanitize_node(item, depth + 1, start) for item in node]

        if isinstance(node, bool):
            return node

        if isinstance(node, int):
            return node

        if isinstance(node, float):
            if node != node or node == float("inf") or node == float("-inf"):
                return "<non-finite-float>"
            return node

        if isinstance(node, str):
            # Truncate before any regex (ReDoS protection), then pattern-scan so
            # secrets embedded under non-sensitive keys are redacted, not just
            # length-bounded. Fail closed if the shared budget is exhausted.
            truncated = node[:SAFE_MESSAGE_MAX_CHARS]
            redacted = self._apply_redaction_patterns(truncated, start)
            if redacted is None:
                return REDACTED_BUDGET
            return redacted[:SAFE_MESSAGE_MAX_CHARS]

        if node is None:
            return None

        # Unsupported type: replace with safe type name
        type_name = f"<{type(node).__name__}>"
        self._incr("sanitizer_unsupported_type_total")
        return type_name
