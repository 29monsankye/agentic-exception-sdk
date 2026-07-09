from pydantic.config import ConfigDict
from _typeshed import Incomplete
from agentic_exception_sdk.taxonomy.enums import AgentExceptionClass, EscalationLevel, ExceptionSource
from datetime import datetime
from pydantic import BaseModel, RootModel
from typing import ClassVar, Any, TypeAlias

__all__ = ['AgentExceptionEnvelope', 'SafeContextSnapshot', 'SafeContextValue']

SafeContextValue: TypeAlias = Any

class SafeContextSnapshot(RootModel[dict[str, Any]]):
    model_config: ClassVar[ConfigDict]
class AgentExceptionEnvelope(BaseModel):
    model_config: ClassVar[ConfigDict]
    exception_id: str = ...
    agent_id: str
    tool_name: str | None
    exception_class: AgentExceptionClass
    source: ExceptionSource
    error_type: str
    message: str
    context_snapshot: SafeContextSnapshot
    suggested_recovery: EscalationLevel
    occurred_at: datetime
    correlation_id: str | None
    sdk_version: str
    attempt_count: int
    lineage: list[str]
