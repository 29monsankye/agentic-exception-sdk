from enum import StrEnum

__all__ = ['AvailabilityMode']

class AvailabilityMode(StrEnum):
    FAIL_CLOSED = 'fail_closed'
    FAIL_OPEN_DEGRADED = 'fail_open_degraded'
