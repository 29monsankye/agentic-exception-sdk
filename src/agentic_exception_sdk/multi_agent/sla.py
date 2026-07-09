"""SLA policy for multi-agent systems.

AgentSLAPolicy tracks wall-clock elapsed time and raises SLAViolationError
when the configured max_seconds deadline is exceeded.

SLAViolationError is imported from taxonomy.errors for re-export so callers
can import from either location.
"""

from __future__ import annotations

import time

from agentic_exception_sdk.taxonomy.errors import SLAViolationError

__all__ = [
    "AgentSLAPolicy",
    "SLAViolationError",
]


class AgentSLAPolicy:
    """Checks whether an agent's elapsed wall-clock time has exceeded its SLA.

    The policy uses time.monotonic() so it is not affected by system clock
    adjustments. Create one policy per agent run; check() raises whenever
    the deadline is exceeded.

    Args:
        max_seconds: Maximum allowed wall-clock seconds for the agent run.
        agent_id: Log-safe identifier for the agent subject to this SLA.
    """

    def __init__(self, *, max_seconds: float, agent_id: str) -> None:
        self._max_seconds = max_seconds
        self._agent_id = agent_id
        self._start_time: float = time.monotonic()

    def check(self) -> None:
        """Assert that the SLA deadline has not been exceeded.

        Raises:
            SLAViolationError: If elapsed time exceeds max_seconds.
        """
        elapsed = time.monotonic() - self._start_time
        if elapsed > self._max_seconds:
            raise SLAViolationError(
                f"agent={self._agent_id} elapsed={elapsed:.3f}s "
                f"exceeded sla={self._max_seconds}s"
            )

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed wall-clock seconds since the policy was created."""
        return time.monotonic() - self._start_time

    def remaining_seconds(self) -> float:
        """Remaining seconds until the SLA deadline.

        Returns:
            Remaining seconds; negative when the deadline has passed.
        """
        return self._max_seconds - self.elapsed_seconds
