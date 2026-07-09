"""Serialization helpers for AgentExceptionEnvelope.

envelope_to_json() / envelope_from_json() use Pydantic v2 strict JSON mode.
envelope_debug_repr() uses json.dumps(sort_keys=True) for human-readable
debugging ONLY.

WARNING: For security-sensitive operations (deduplication, signatures, DLQ keys,
hashing), use RFC 8785 JSON Canonicalization Scheme (JCS) via the ``jcs``
package — NOT json.dumps(sort_keys=True). The debug repr is not canonical.
"""

from __future__ import annotations

import hashlib
import json
from typing import cast

from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "envelope_canonical_bytes",
    "envelope_canonical_sha256",
    "envelope_debug_repr",
    "envelope_leaf_hash",
    "envelope_from_json",
    "envelope_to_json",
]


def envelope_to_json(envelope: AgentExceptionEnvelope) -> str:
    """Serialize an envelope to JSON using Pydantic strict mode.

    Uses model_dump_json() which respects datetime serialization, enum values,
    and frozen model invariants. Output is a single-line JSON string.

    Args:
        envelope: The exception envelope to serialize.

    Returns:
        A compact JSON string representation of the envelope.
    """
    return envelope.model_dump_json()


def envelope_from_json(json_str: str) -> AgentExceptionEnvelope:
    """Deserialize an envelope from a JSON string using Pydantic strict validation.

    Re-runs all field and model validators, including class/level consistency
    and safe-identifier checks. Malformed or tampered JSON raises ValidationError.

    Args:
        json_str: A JSON string produced by envelope_to_json().

    Returns:
        A fully validated AgentExceptionEnvelope instance.

    Raises:
        pydantic.ValidationError: If the JSON does not conform to the envelope schema.
        json.JSONDecodeError: If json_str is not valid JSON.
    """
    return AgentExceptionEnvelope.model_validate_json(json_str)


def envelope_canonical_bytes(envelope: AgentExceptionEnvelope) -> bytes:
    """Return RFC 8785 JCS canonical bytes for the envelope dedup key.

    This helper intentionally excludes ``attempt_count`` so retry attempts for
    the same logical failure collapse to the same deduplication key. Do not use
    it as an integrity leaf; use ``envelope_leaf_hash()`` for full-envelope
    integrity hashing.

    Args:
        envelope: The exception envelope to canonicalize.

    Returns:
        RFC 8785 canonical JSON bytes.
    """
    import jcs

    canonical = jcs.canonicalize(envelope.model_dump(mode="json", exclude={"attempt_count"}))
    if isinstance(canonical, bytes):
        return canonical
    return cast("str", canonical).encode("utf-8")


def envelope_canonical_sha256(envelope: AgentExceptionEnvelope) -> str:
    """Return SHA-256 hex digest for the envelope dedup key.

    Args:
        envelope: The exception envelope to hash.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(envelope_canonical_bytes(envelope)).hexdigest()


def envelope_leaf_hash(envelope: AgentExceptionEnvelope) -> str:
    """Return SHA-256 integrity hash over the full canonical envelope.

    Unlike ``envelope_canonical_sha256()``, this includes ``attempt_count`` and
    every other serialized field. It is suitable for Merkle leaves and audit
    integrity checkpoints, not deduplication.
    """
    import jcs

    canonical = jcs.canonicalize(envelope.model_dump(mode="json"))
    if not isinstance(canonical, bytes):
        canonical = cast("str", canonical).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def envelope_debug_repr(envelope: AgentExceptionEnvelope) -> str:
    """Return a human-readable JSON representation of the envelope for debugging.

    Produces indented, key-sorted JSON using json.dumps. Suitable for log
    inspection and development tooling only.

    WARNING: Do NOT use this for hashing, deduplication, signatures, or DLQ keys.
    Use ``envelope_canonical_sha256()`` for deduplication and
    ``envelope_leaf_hash()`` for full-envelope integrity hashing.

    Args:
        envelope: The exception envelope to represent.

    Returns:
        An indented, key-sorted JSON string for human consumption.
    """
    return json.dumps(envelope.model_dump(mode="json"), sort_keys=True, indent=2)
