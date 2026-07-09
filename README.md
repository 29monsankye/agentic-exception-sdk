# agentic-exception-sdk

A domain-agnostic exception management SDK for agentic systems. Classifies
failures into three tiers, routes them through pluggable handlers, wraps tool
boundaries with resilient call wrappers, and provides safe no-op defaults for
zero-config adoption.

Designed as a **standalone Python package** installed and wired into host
projects via constructor injection. Contains no host-specific imports, no global
state, and no module-level singletons.

**Python 3.11+** — no `exceptiongroup` backport required.

---

## Three-Tier Classification

Every failure is assigned to exactly one class:

| Class | Meaning | Action |
|---|---|---|
| `EXCEPTION` | Gracefully resolvable | Automatic retry or fallback — no human involved |
| `ISSUE` | Requires intervention | Agent pauses; checkpoint handoff or human escalation |
| `HARD_KILL` | Not resolvable | Agent terminates immediately; envelope written to DLQ |

`HARD_KILL` raises `AgentHardKillError(BaseException)` — it is never caught by
`except Exception` anywhere in the call stack.

---

## Install

```bash
# Core only (pydantic + RFC 8785 JCS canonicalization)
pip install agentic-exception-sdk

# With OpenTelemetry export
pip install agentic-exception-sdk[otel]

# With Redis circuit-breaker state
pip install agentic-exception-sdk[redis]

# With Prometheus metrics
pip install agentic-exception-sdk[prometheus]

# With structlog JSON renderer
pip install agentic-exception-sdk[structlog]

# Everything
pip install agentic-exception-sdk[all]
```

> **ReDoS protection:** Install with `pip install "agentic-exception-sdk[re2]"` to
> enable linear-time google-re2 redaction. Without it the SDK falls back to stdlib
> `re` with patterns designed to avoid catastrophic backtracking.

---

## Quick Start

```python
from agentic_exception_sdk import (
    AgentHardKillError,
    NoOpResilienceBundle,
    SafeContextSnapshot,
    resilient,
)

# Zero-config: every component is a safe no-op
bundle = NoOpResilienceBundle()

# Wrap any tool boundary with the curried factory:
#   resilient(bundle, ...)(fn)(*args, **kwargs)
result = resilient(
    bundle,
    tool_name="search_flights",
    agent_id="booking-agent",
    correlation_id="req-abc123",
    context_snapshot=SafeContextSnapshot({"booking_id": "BK-001"}),
    fallback_value=[],          # returned on EXCEPTION or ISSUE if set
)(flight_client.search)(origin, destination, dates)

# Catch HARD_KILL only at the outermost executor boundary
try:
    executor.run(booking_request)
except AgentHardKillError as hk:
    audit.emit(hk.envelope.exception_id)
    raise
```

Wire a real `ResilienceBundle` in the composition root when you need retries,
circuit breaking, budget enforcement, or escalation routing:

```python
from agentic_exception_sdk import (
    AgentBudget,
    BudgetWatchdog,
    ExponentialBackoffRetry,
    InMemoryCircuitBreaker,
    InMemoryMetricsCollector,
    ResilienceBundle,
)

bundle = ResilienceBundle(
    retry_policy=ExponentialBackoffRetry(
        max_attempts=3,
        base_delay_seconds=0.5,
        jitter=True,
        retryable_exceptions=(TimeoutError, ConnectionError),
    ),
    circuit_breaker=InMemoryCircuitBreaker(
        failure_threshold=5,
        cooldown_seconds=30,
    ),
    agent_budget=BudgetWatchdog(AgentBudget(
        max_seconds=45.0,
        max_tool_calls=10,
        failure_budget=3,
        max_total_cost_micros_usd=2_000_000,  # $2.00
    )),
    metrics_collector=InMemoryMetricsCollector(),  # swap for PrometheusMetricsCollector
)
```

---

## Key Concepts

- **`ResilienceBundle`** — the single injection object. Every field defaults to
  a safe no-op. Inject only what you need.
- **`NoOpResilienceBundle()`** — returns a fully defaulted bundle. Use as the
  default when no bundle is injected.
- **`resilient()` / `async_resilient()`** — curried factory wrappers. Preserve
  wrapped callable parameter types via `ParamSpec`. Use `async_resilient()` with
  `asyncio.timeout()` for async tool calls and LLM calls with timeouts.
- **`AgentHardKillError(BaseException)`** — raised on `HARD_KILL`. Propagates
  past all `except Exception` handlers. Catch only at the outermost executor
  boundary.
- **`SafeContextSnapshot`** — frozen Pydantic `RootModel` for agent state.
  Enforces JSON-safe values, max depth 5, max 100 keys per dict, and rejects
  non-finite floats.
- **`extend_lineage()`** — validated factory for multi-agent envelope
  propagation. Caps lineage at 64 hops; exceeding the cap auto-promotes to
  `HARD_KILL`.
- **`TrustBoundaryValidator`** — runs pattern-based redaction (secrets, tokens,
  PANs, bidi controls) over exception **messages**. For context snapshots it
  redacts values under sensitively-named keys (`password`, `token`, `secret`,
  …) and bounds size/depth. **Note:** snapshot values under non-sensitive keys
  are length-bounded but **not** pattern-scanned — do not place raw secrets in
  arbitrary snapshot fields. See **Limitations & Scope**.
- **`ResilienceBundle.metrics_collector`** — optional `MetricsCollector`
  (protocol). `PrometheusMetricsCollector` emits `agent_exceptions_total`,
  `agent_hard_kills_total`, `agent_retries_total`, `agent_budget_exhausted_total`.
- **`ResilienceBundle.meter_provider`** — optional OTel `MeterProvider`. When
  set, creates an `agent_tool_call_duration_seconds` histogram per call.
- **`ResilienceBundle.dlq`** — `DeadLetterQueue` for HARD_KILL envelopes.
  Defaults to `InMemoryDLQ` (ring buffer, drop-oldest). Swap for `AsyncInMemoryDLQ`
  in async contexts, or a satellite-package adapter (SQS, Pub/Sub, Redis).
- **`ResilienceBundle.recovery_policy`** — optional post-classification recovery
  hook called before the default fallback/escalation path on EXCEPTION and ISSUE
  tiers. Satellite package `agentic-exception-retry` supplies `AdaptiveRetryPolicy`
  and `CircuitAwareRetryPolicy`.
- **`ResilienceBundle.async_circuit_breaker`** — optional async circuit breaker
  used by `async_resilient()`. Falls back to sync `circuit_breaker` when not set.
- **`allow_sync_llm_timeout`** — opt-in flag on `resilient()` that runs `fn` in
  a bounded `ThreadPoolExecutor` with the given `timeout_seconds`. The leaked
  thread cannot be cancelled and may continue spending tokens after the wrapper
  returns; prefer `async_resilient()` with `asyncio.timeout()` for LLM calls.
- **`PersistenceProvider` / `NullProvider`** — the audit-persistence boundary.
  Every envelope carries a full-envelope integrity leaf hash (`envelope_leaf_hash()`),
  distinct from the `envelope_canonical_sha256()` dedup key. The default in-process
  `NullProvider` computes real leaf hashes and in-memory Merkle roots but emits
  unsigned, non-durable checkpoints. `sentirock.attestation()` reports the active
  provider's capabilities (`durable`, `checkpoint_signing`, `worm`).

## Integrity & audit status

Every governed call emits a leaf hash over the redacted envelope and participates
in an in-memory Merkle log. In the default (in-process) provider these checkpoints
are unsigned and non-durable — the SDK's attestation API and span attributes
(`sentirock.provider.durable`, `sentirock.audit.degraded`) report this explicitly.
Durable, signed, WORM-retained audit trails are delivered by the persistence
substrate on our enterprise roadmap; the `PersistenceProvider` interface it plugs
into is already published in this repo (the conformance suite that will gate full
attestation is planned).

## Streaming Budget Enforcement

```python
from agentic_exception_sdk import AgentBudget, BudgetWatchdog
from agentic_exception_sdk.budget import StreamingBudgetGuard

watchdog = BudgetWatchdog(AgentBudget(max_output_tokens=100))
stream = [{"text": "hi", "tokens": 1}, {"text": " there", "tokens": 2}]

for chunk in StreamingBudgetGuard.wrap_sync_stream(
    stream,
    watchdog,
    tool_name="openai-chat",
    agent_id="agent-1",
    token_extractor=lambda c: c["tokens"],
):
    print(chunk["text"])
```

---

## Limitations & Scope

The SDK's controls are **mitigations, not guarantees**, and are not a substitute for defense-in-depth. Please read this before relying on the SDK for a security- or compliance-critical outcome:

- **Redaction is best-effort.** The trust-boundary sanitizer is pattern-based; novel, split, or obfuscated secrets can pass through. It is not a substitute for a dedicated DLP control, and should not be your sole barrier against secret exposure.
- **`HARD_KILL` is in-execution termination, not an operator kill switch.** `AgentHardKillError` stops the agent's *own run* when a tripwire fires. It is **not** an out-of-band control that deactivates a running agent, agent type, or fleet on demand — that capability is on the roadmap. Do not represent it as a fleet-wide or human-pullable deactivation switch.
- **`AgentHardKillError` extends `BaseException` — mind your runtime.** This is deliberate (it survives `except Exception`), but runtimes that treat `BaseException` as fatal (Celery workers, some ASGI/thread-pool executors) may tear down the worker when it propagates. Catch it at your outermost executor boundary and translate it to your runtime's shutdown/abort convention; do not let it escape uncaught into a shared worker pool.
- **Integrity hashing is not a durable audit trail by default.** The in-process provider emits unsigned, non-durable checkpoints and attests this via `sentirock.attestation()` (`durable=false`). Durable, signed, WORM-retained audit is delivered by the enterprise substrate on the roadmap.
- **Not a compliance certification.** Any coverage mapping (e.g. to the OWASP LLM Top 10) documents what the SDK *does*; it does not make your system compliant with any regulation. Compliance is a property of your overall system and processes.
- **No warranty.** Provided under the MIT License, **"AS IS," without warranty of any kind** (see [LICENSE](LICENSE)). You are responsible for validating its behavior in your own environment.

---

## License

MIT License — see [LICENSE](LICENSE). The SDK is provided "AS IS", without warranty of any kind; the authors are not liable for any claim or damages arising from its use. See **Limitations & Scope** above.
