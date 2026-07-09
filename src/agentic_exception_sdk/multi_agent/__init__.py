"""Multi-agent: orchestrator router, consensus gate, and SLA policy."""

from __future__ import annotations

from agentic_exception_sdk.multi_agent.consensus import (
    ConsensusGate,
    ConsensusNotReachedError,
)
from agentic_exception_sdk.multi_agent.parallel import (
    AgentOutcome,
    FanOutOutcomeEnvelope,
    call_parallel,
)
from agentic_exception_sdk.multi_agent.router import (
    OrchestratorExceptionRouter,
    WorkerHandler,
    fan_out,
)
from agentic_exception_sdk.multi_agent.sla import AgentSLAPolicy, SLAViolationError

__all__ = [
    "AgentSLAPolicy",
    "AgentOutcome",
    "ConsensusGate",
    "ConsensusNotReachedError",
    "FanOutOutcomeEnvelope",
    "OrchestratorExceptionRouter",
    "SLAViolationError",
    "WorkerHandler",
    "call_parallel",
    "fan_out",
]
