from pydantic.config import ConfigDict
from _typeshed import Incomplete
from agentic_exception_sdk.bundle import ResilienceBundle
from agentic_exception_sdk.taxonomy.enums import AgentExceptionClass
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope
from collections.abc import Callable
from pydantic import BaseModel
from typing import ClassVar, Any, Literal

__all__ = ['AgentOutcome', 'FanOutOutcomeEnvelope', 'call_parallel']

class AgentOutcome(BaseModel):
    model_config: ClassVar[ConfigDict]
    agent_id: str
    tool_name: str
    status: Literal['success', 'exception']
    result: Any
    envelope: AgentExceptionEnvelope | None

class FanOutOutcomeEnvelope(BaseModel):
    model_config: ClassVar[ConfigDict]
    fan_out_id: str
    correlation_id: str | None
    outcomes: list[AgentOutcome]
    aggregate_class: AgentExceptionClass | None
    success_count: int
    failure_count: int
    summary: dict[str, int]

class _EnvelopeCaptureBus:
    envelopes: list[AgentExceptionEnvelope]
    def __init__(self, delegate: object) -> None: ...
    def publish(self, envelope: AgentExceptionEnvelope) -> None: ...

async def call_parallel(calls: list[tuple[ResilienceBundle, str, str, Callable[[], Any]]], *, correlation_id: str | None = None, timeout_seconds: float | None = None) -> tuple[list[Any], FanOutOutcomeEnvelope]: ...
