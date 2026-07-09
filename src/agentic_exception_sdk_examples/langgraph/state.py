from __future__ import annotations

from typing import NotRequired, TypedDict


class TravelStateDict(TypedDict):
    correlation_id: str
    fail_mode: str
    search_attempts: int
    flights: list[str]
    selected_flight: str | None
    confirmation: str | None
    error_summary: str | None


class TravelState(TravelStateDict, total=False):
    pass


class AttrTravelState(dict[str, object]):
    # LangGraph passes dict state, while resilient_node reads/writes attributes.
    def __getattr__(self, name: str) -> object:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: object) -> None:
        self[name] = value


class ScenarioResult(TypedDict):
    state: TravelState
    envelopes: list[dict[str, str]]


class ScenarioInput(TypedDict):
    name: str
    fail_mode: NotRequired[str]


def make_state(*, correlation_id: str, fail_mode: str = "") -> AttrTravelState:
    return AttrTravelState(
        correlation_id=correlation_id,
        fail_mode=fail_mode,
        search_attempts=0,
        flights=[],
        selected_flight=None,
        confirmation=None,
        error_summary=None,
    )
