from enum import StrEnum

__all__ = ['AvailabilityMode']

class AvailabilityMode(StrEnum):
    FAIL_CLOSED: str
    FAIL_OPEN_DEGRADED: str
