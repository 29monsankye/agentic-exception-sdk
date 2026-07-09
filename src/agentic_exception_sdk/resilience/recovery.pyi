from agentic_exception_sdk.escalation.router import RecoveryDirective
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope
from typing import Protocol

__all__ = ['RecoveryPolicy', 'SuggestedRecoveryPolicy']

class RecoveryPolicy(Protocol):
    def recover(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None: ...

class SuggestedRecoveryPolicy:
    def recover(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None: ...
