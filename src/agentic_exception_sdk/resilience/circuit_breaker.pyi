from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeVar

__all__ = ['AsyncCircuitBreaker', 'AsyncInMemoryCircuitBreaker', 'CircuitBreaker', 'CircuitState', 'InMemoryCircuitBreaker', 'NoOpAsyncCircuitBreaker', 'NoOpCircuitBreaker', 'RedisCircuitBreaker']

T = TypeVar('T')

class CircuitState(Enum):
    CLOSED = 'closed'
    OPEN = 'open'
    HALF_OPEN = 'half_open'

class CircuitBreaker(Protocol):
    def call(self, fn: Callable[[], T]) -> T: ...

class AsyncCircuitBreaker(Protocol):
    async def call(self, fn: Callable[[], Awaitable[T]]) -> T: ...

class NoOpCircuitBreaker:
    def call(self, fn: Callable[[], T]) -> T: ...
    async def async_call(self, fn: Callable[[], Awaitable[T]]) -> T: ...

class NoOpAsyncCircuitBreaker:
    async def call(self, fn: Callable[[], Awaitable[T]]) -> T: ...

class InMemoryCircuitBreaker:
    state_transition_total: int
    def __init__(self, *, failure_threshold: int = 5, cooldown_seconds: float = 30.0, half_open_probe_count: int = 1) -> None: ...
    @property
    def state(self) -> CircuitState: ...
    def call(self, fn: Callable[[], T]) -> T: ...
    async def async_call(self, fn: Callable[[], Awaitable[T]]) -> T: ...

class AsyncInMemoryCircuitBreaker:
    state_transition_total: int
    def __init__(self, *, failure_threshold: int = 5, cooldown_seconds: float = 30.0, half_open_probe_count: int = 1) -> None: ...
    @property
    def state(self) -> CircuitState: ...
    async def call(self, fn: Callable[[], Awaitable[T]]) -> T: ...

@dataclass(frozen=True)
class _RedisState:
    state: CircuitState
    failure_count: int
    probe_success_count: int
    opened_at: float | None

class RedisCircuitBreaker:
    state_transition_total: int
    def __init__(self, *, redis_url: str, failure_threshold: int = 5, cooldown_seconds: float = 30.0, half_open_probe_count: int = 1, state_unavailable_retry_budget: int = 3, circuit_name: str = 'default', environment: str = 'dev', sdk_version: str = '1.1.0', redis_client: object | None = None) -> None: ...
    @property
    def key(self) -> str: ...
    @property
    def state_unavailable_count(self) -> int: ...
    def call(self, fn: Callable[[], T]) -> T: ...
    async def async_call(self, fn: Callable[[], Awaitable[T]]) -> T: ...
