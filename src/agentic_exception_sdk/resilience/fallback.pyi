from collections.abc import Callable
from typing import Any, Protocol, TypeVar

__all__ = ['FallbackChain', 'NoOpFallback', 'OrderedFallbackChain']

T = TypeVar('T')

class FallbackChain(Protocol):
    def execute(self, *args: Any, **kwargs: Any) -> Any: ...

class NoOpFallback:
    def execute(self, *args: Any, **kwargs: Any) -> None: ...

class OrderedFallbackChain:
    def __init__(self, callables: list[Callable[..., Any]]) -> None: ...
    def execute(self, *args: Any, **kwargs: Any) -> Any: ...
