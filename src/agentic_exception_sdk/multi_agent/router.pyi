from agentic_exception_sdk.escalation.router import RecoveryDirective
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope
from collections.abc import Awaitable, Callable

__all__ = ['OrchestratorExceptionRouter', 'WorkerHandler', 'fan_out']

WorkerHandler = Callable[[AgentExceptionEnvelope], Awaitable[RecoveryDirective | None]]

async def fan_out(envelope: AgentExceptionEnvelope, handlers: list[WorkerHandler], *, timeout_seconds: float | None = None) -> list[RecoveryDirective | None]: ...

class OrchestratorExceptionRouter:
    def __init__(self, handlers: list[WorkerHandler] | None = None, *, fan_out_timeout_seconds: float | None = None) -> None: ...
    def register(self, handler: WorkerHandler) -> None: ...
    async def route(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None: ...
