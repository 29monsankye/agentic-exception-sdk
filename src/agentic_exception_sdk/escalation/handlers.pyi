from agentic_exception_sdk.escalation.router import RecoveryDirective
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = ['NoOpHandler']

class NoOpHandler:
    def handle(self, envelope: AgentExceptionEnvelope) -> RecoveryDirective | None: ...
