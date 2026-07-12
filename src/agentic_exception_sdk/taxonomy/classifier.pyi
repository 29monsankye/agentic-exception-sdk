from agentic_exception_sdk.taxonomy.enums import AgentExceptionClass, EscalationLevel, ExceptionSource

_Classification = tuple[AgentExceptionClass, ExceptionSource, EscalationLevel]

__all__ = ['ExceptionClassifier']

class ExceptionClassifier:
    def classify(self, exc: BaseException) -> _Classification: ...
