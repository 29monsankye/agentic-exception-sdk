from __future__ import annotations

from typing import Any

from agentic_exception_sdk import (
    ExponentialBackoffRetry,
    InMemoryBus,
    PromptInjectionError,
    ResilienceBundle,
)

from agentic_exception_adapters import ResilientBaseTool

try:
    import crewai as _crewai
except ImportError:
    _crewai = None


def crewai_available() -> bool:
    return _crewai is not None


class FlightSearchTool(ResilientBaseTool):
    name: str = "search-flights"
    description: str = "Search deterministic flight inventory"
    fail_on_call: bool = False
    recover_on_retry: bool = False
    hard_kill_on_call: bool = False
    attempts: int = 0

    def __init__(
        self,
        *,
        bus: InMemoryBus,
        correlation_id: str = "corr-crewai",
        fail_on_call: bool = False,
        recover_on_retry: bool = False,
        hard_kill_on_call: bool = False,
    ) -> None:
        retry = ExponentialBackoffRetry(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter=False,
        )
        bundle = ResilienceBundle(propagation_bus=bus, retry_policy=retry)
        super().__init__(
            bundle=bundle,
            correlation_id=correlation_id,
            fallback_value="fallback-flight:BA-404",
            fail_on_call=fail_on_call,
            recover_on_retry=recover_on_retry,
            hard_kill_on_call=hard_kill_on_call,
            attempts=0,
        )

    def _run_resilient(self, destination: str = "LHR", **_: Any) -> str:
        self.attempts += 1
        if self.hard_kill_on_call:
            raise PromptInjectionError("prompt injection detected")
        if self.fail_on_call and self.recover_on_retry and self.attempts == 1:
            raise TimeoutError("flight provider transient timeout")
        if self.fail_on_call and not self.recover_on_retry:
            raise TimeoutError("flight provider unavailable")
        return f"flight:BA-282:{destination}"


class HotelSearchTool(ResilientBaseTool):
    name: str = "search-hotels"
    description: str = "Search deterministic hotel inventory"
    fail_on_call: bool = False
    hard_kill_on_call: bool = False
    attempts: int = 0

    def __init__(
        self,
        *,
        bus: InMemoryBus,
        correlation_id: str = "corr-crewai",
        fail_on_call: bool = False,
        hard_kill_on_call: bool = False,
    ) -> None:
        retry = ExponentialBackoffRetry(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter=False,
        )
        bundle = ResilienceBundle(propagation_bus=bus, retry_policy=retry)
        super().__init__(
            bundle=bundle,
            correlation_id=correlation_id,
            fallback_value="fallback-hotel:manual-review",
            fail_on_call=fail_on_call,
            hard_kill_on_call=hard_kill_on_call,
            attempts=0,
        )

    def _run_resilient(self, city: str = "London", **_: Any) -> str:
        self.attempts += 1
        if self.hard_kill_on_call:
            raise PromptInjectionError("prompt injection detected")
        if self.fail_on_call:
            raise TimeoutError("hotel provider unavailable")
        return f"hotel:Southbank:{city}"


def build_tools(
    *,
    bus: InMemoryBus,
    correlation_id: str = "corr-crewai",
    fail_on_call: bool = False,
    recover_on_retry: bool = False,
    hard_kill_on_call: bool = False,
) -> tuple[FlightSearchTool, HotelSearchTool]:
    return (
        FlightSearchTool(
            bus=bus,
            correlation_id=correlation_id,
            fail_on_call=fail_on_call,
            recover_on_retry=recover_on_retry,
            hard_kill_on_call=hard_kill_on_call,
        ),
        HotelSearchTool(bus=bus, correlation_id=correlation_id),
    )
