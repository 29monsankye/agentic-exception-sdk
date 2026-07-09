from __future__ import annotations

from agentic_exception_sdk.resilience.retry import ExponentialBackoffRetry


def test_exponential_backoff_retry_scheduling_overhead_jitter_disabled(benchmark) -> None:
    retry = ExponentialBackoffRetry(
        max_attempts=1,
        base_delay_seconds=0,
        jitter=False,
    )

    def call() -> str:
        return retry.execute(lambda: "ok")

    assert benchmark(call) == "ok"
