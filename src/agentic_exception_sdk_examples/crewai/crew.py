from __future__ import annotations

from typing import Any

from agentic_exception_sdk import InMemoryBus
from agentic_exception_sdk_examples.crewai.tools import build_tools


def _load_crewai() -> tuple[type[Any], type[Any], type[Any]]:
    try:
        from crewai import Agent, Crew, Task
    except ImportError as exc:
        raise ImportError(
            "Install CrewAI support with: pip install agentic-exception-adapters[crewai]"
        ) from exc
    return Agent, Crew, Task


def build_crew(
    *,
    bus: InMemoryBus,
    correlation_id: str = "corr-crewai",
    fail_on_call: bool = False,
) -> Any:
    Agent, Crew, Task = _load_crewai()
    flight_tool, hotel_tool = build_tools(
        bus=bus,
        correlation_id=correlation_id,
        fail_on_call=fail_on_call,
    )
    agent = Agent(
        role="Travel booking agent",
        goal="Return a deterministic travel booking summary",
        backstory="A test-only agent that uses fixed local tools.",
        tools=[flight_tool, hotel_tool],
        llm=False,
        verbose=False,
    )
    task = Task(
        description="Search for a flight to LHR and a hotel in London.",
        expected_output="A booking summary containing one flight and one hotel.",
        agent=agent,
    )
    return Crew(agents=[agent], tasks=[task], verbose=False)


def run_scenario(
    *,
    correlation_id: str = "corr-crewai",
    fail_on_call: bool = False,
    recover_on_retry: bool = False,
    hard_kill_on_call: bool = False,
) -> dict[str, object]:
    bus = InMemoryBus()
    flight_tool, hotel_tool = build_tools(
        bus=bus,
        correlation_id=correlation_id,
        fail_on_call=fail_on_call,
        recover_on_retry=recover_on_retry,
        hard_kill_on_call=hard_kill_on_call,
    )
    result = {
        "flight": flight_tool._run("LHR"),
        "hotel": hotel_tool._run("London"),
        "flight_attempts": flight_tool.attempts,
        "hotel_attempts": hotel_tool.attempts,
    }
    envelopes = [
        {
            "tool_name": envelope.tool_name,
            "exception_class": envelope.exception_class.value,
            "correlation_id": envelope.correlation_id,
        }
        for envelope in bus.drain()
    ]
    return {"result": result, "envelopes": envelopes}
