from __future__ import annotations

from agentic_exception_sdk import AgentHardKillError
from agentic_exception_sdk_examples.langgraph.graph import run_scenario
from agentic_exception_sdk_examples.langgraph.state import ScenarioInput


def main() -> None:
    scenarios: list[ScenarioInput] = [
        {"name": "happy-path"},
        {"name": "retry-success", "fail_mode": "retry-success"},
        {"name": "retry-fallback", "fail_mode": "timeout"},
        {"name": "guardrail-hard-kill", "fail_mode": "hard-kill"},
    ]
    for scenario in scenarios:
        name = scenario["name"]
        try:
            result = run_scenario(
                fail_mode=scenario.get("fail_mode", ""),
                correlation_id=f"corr-{name}",
            )
            print(f"{name}: state={result['state']} envelopes={result['envelopes']}")
        except AgentHardKillError as exc:
            envelope = exc.envelope
            print(
                f"{name}: hard_kill tool={envelope.tool_name} "
                f"class={envelope.exception_class.value} correlation_id={envelope.correlation_id}"
            )


if __name__ == "__main__":
    main()
