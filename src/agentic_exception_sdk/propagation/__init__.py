"""Propagation: bus, dead-letter queue, and serialization protocol."""

from __future__ import annotations

from agentic_exception_sdk.propagation.bus import (
    AsyncExceptionPropagationBus,
    AsyncInMemoryBus,
    ExceptionPropagationBus,
    InMemoryBus,
    PropagationBufferFullError,
)
from agentic_exception_sdk.propagation.dlq import (
    AsyncInMemoryDLQ,
    DeadLetterQueue,
    InMemoryDLQ,
)
from agentic_exception_sdk.propagation.protocol import (
    envelope_canonical_bytes,
    envelope_canonical_sha256,
    envelope_debug_repr,
    envelope_from_json,
    envelope_to_json,
)

__all__ = [
    "AsyncExceptionPropagationBus",
    "AsyncInMemoryBus",
    "AsyncInMemoryDLQ",
    "DeadLetterQueue",
    "ExceptionPropagationBus",
    "InMemoryBus",
    "InMemoryDLQ",
    "PropagationBufferFullError",
    "envelope_canonical_bytes",
    "envelope_canonical_sha256",
    "envelope_debug_repr",
    "envelope_from_json",
    "envelope_to_json",
]
