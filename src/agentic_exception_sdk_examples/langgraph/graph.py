from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentic_exception_sdk import (
    ExponentialBackoffRetry,
    InMemoryBus,
    PromptInjectionError,
    ResilienceBundle,
)

from agentic_exception_adapters import resilient_node
from agentic_exception_sdk_examples.langgraph.state import (
    AttrTravelState,
    ScenarioResult,
    TravelState,
    TravelStateDict,
    make_state,
)


def _load_langgraph() -> tuple[type[Any], str]:
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise ImportError(
            "Install LangGraph support with: pip install agentic-exception-adapters[langgraph]"
        ) from exc
    return StateGraph, END


def _envelope_summary(bus: InMemoryBus) -> list[dict[str, str]]:
    return [
        {
            "tool_name": envelope.tool_name,
            "exception_class": envelope.exception_class.value,
            "correlation_id": envelope.correlation_id,
        }
        for envelope in bus.drain()
    ]


def create_graph(
    *,
    bus: InMemoryBus | None = None,
) -> tuple[Callable[[TravelState], TravelState], InMemoryBus]:
    StateGraph, END = _load_langgraph()
    propagation_bus = bus or InMemoryBus()
    retry = ExponentialBackoffRetry(
        max_attempts=2,
        base_delay_seconds=0,
        max_delay_seconds=0,
        jitter=False,
    )
    bundle = ResilienceBundle(propagation_bus=propagation_bus, retry_policy=retry)

    @resilient_node(
        bundle,
        tool_name="search-flights",
        agent_id="travel-langgraph-agent",
        fallback_value=["BA-404"],
        fallback_field="flights",
    )
    def search_flights_body(state: AttrTravelState) -> AttrTravelState:
        fail_mode = state.get("fail_mode", "")
        attempts = int(state.get("search_attempts", 0)) + 1
        state.search_attempts = attempts
        if fail_mode == "timeout":
            raise TimeoutError("flight provider unavailable")
        if fail_mode == "retry-success" and attempts == 1:
            raise TimeoutError("flight provider transient timeout")
        if fail_mode == "hard-kill":
            raise PromptInjectionError("prompt injection detected")
        state.flights = ["BA-282", "VS-20"]
        return state

    def search_flights(state: TravelState) -> TravelState:
        return TravelState(search_flights_body(AttrTravelState(state)))

    @resilient_node(
        bundle,
        tool_name="choose-flight",
        agent_id="travel-langgraph-agent",
        fallback_value="BA-404",
        fallback_field="selected_flight",
    )
    def choose_flight_body(state: AttrTravelState) -> AttrTravelState:
        flights = state.get("flights", [])
        state.selected_flight = flights[0] if isinstance(flights, list) and flights else "BA-404"
        return state

    def choose_flight(state: TravelState) -> TravelState:
        return TravelState(choose_flight_body(AttrTravelState(state)))

    @resilient_node(
        bundle,
        tool_name="confirm-booking",
        agent_id="travel-langgraph-agent",
        fallback_value="manual-review",
        fallback_field="confirmation",
    )
    def confirm_booking_body(state: AttrTravelState) -> AttrTravelState:
        selected = state.get("selected_flight") or "BA-404"
        state.confirmation = f"confirmed:{selected}"
        return state

    def confirm_booking(state: TravelState) -> TravelState:
        return TravelState(confirm_booking_body(AttrTravelState(state)))

    graph = StateGraph(TravelStateDict)
    graph.add_node("search_flights", search_flights)
    graph.add_node("choose_flight", choose_flight)
    graph.add_node("confirm_booking", confirm_booking)
    graph.set_entry_point("search_flights")
    graph.add_edge("search_flights", "choose_flight")
    graph.add_edge("choose_flight", "confirm_booking")
    graph.add_edge("confirm_booking", END)
    compiled = graph.compile()
    return compiled.invoke, propagation_bus


def run_scenario(*, fail_mode: str = "", correlation_id: str = "corr-langgraph") -> ScenarioResult:
    invoke, bus = create_graph()
    state = make_state(correlation_id=correlation_id, fail_mode=fail_mode)
    result = invoke(state)
    return {"state": TravelState(dict(result)), "envelopes": _envelope_summary(bus)}
