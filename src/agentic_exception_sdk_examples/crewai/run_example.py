from __future__ import annotations

from agentic_exception_sdk import AgentHardKillError

from agentic_exception_sdk_examples.crewai.crew import run_scenario


def main() -> None:
    scenarios = [
        ("happy-path", False, False),
        ("provider-timeout-retry-success", True, True),
        ("provider-timeout-retry-fallback", True, False),
    ]
    for name, fail_on_call, recover_on_retry in scenarios:
        try:
            result = run_scenario(
                correlation_id=f"corr-{name}",
                fail_on_call=fail_on_call,
                recover_on_retry=recover_on_retry,
            )
            print(f"{name}: result={result['result']} envelopes={result['envelopes']}")
        except AgentHardKillError as exc:
            envelope = exc.envelope
            print(
                f"{name}: hard_kill tool={envelope.tool_name} "
                f"class={envelope.exception_class.value} correlation_id={envelope.correlation_id}"
            )


if __name__ == "__main__":
    main()
