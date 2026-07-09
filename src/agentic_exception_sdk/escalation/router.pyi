from agentic_exception_sdk.taxonomy.enums import EscalationLevel
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope
from dataclasses import dataclass
from typing import Any, Literal, Protocol

__all__ = ['EscalationHandlerLike', 'EscalationRouter', 'NoOpEscalationRouter', 'RecoveryDirective']

@dataclass(frozen=True)
class RecoveryDirective:
    action: Literal['resume', 'escalate', 'abort']
    resume_state: Any | None = ...
    resume_from_step: str | None = ...

class EscalationHandlerLike(Protocol):
    def handle(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None: ...

class NoOpEscalationRouter:
    def route(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None: ...

class EscalationRouter:
    def __init__(self, exception_handlers: dict[EscalationLevel, EscalationHandlerLike] | None = None, issue_handlers: dict[EscalationLevel, EscalationHandlerLike] | None = None, hard_kill_handler: EscalationHandlerLike | None = None) -> None: ...
    def route(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None: ...
