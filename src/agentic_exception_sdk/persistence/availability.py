"""Availability policy for audit persistence."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["AvailabilityMode"]


class AvailabilityMode(StrEnum):
    """How SDK callers should behave when durable audit persistence is unavailable."""

    FAIL_CLOSED = "fail_closed"
    FAIL_OPEN_DEGRADED = "fail_open_degraded"
