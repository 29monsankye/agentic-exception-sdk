from pydantic import BaseModel
from typing import Any, Protocol, TypeVar

__all__ = ['NoOpGate', 'OutputValidationGate', 'PydanticValidationGate']

T = TypeVar('T')

class OutputValidationGate(Protocol):
    def validate(self, result: Any) -> Any: ...

class NoOpGate:
    def validate(self, result: Any) -> Any: ...

class PydanticValidationGate:
    def __init__(self, schema: type[BaseModel]) -> None: ...
    def validate(self, result: Any) -> Any: ...
