# agentic-exception-sdk

A domain-agnostic exception management SDK for agentic systems. Classifies the
exceptions your code raises into three tiers, routes them through pluggable
handlers, wraps tool boundaries with resilient call wrappers, and provides safe
no-op defaults for zero-config adoption.

The SDK governs what happens **after** a failure surfaces as a raised exception.
It does not detect failures itself — see
[What the classifier does and does not do](#what-the-classifier-does-and-does-not-do).

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

### What the classifier does and does not do

The classifier maps **exception types your code raises** onto these tiers. It is
type dispatch, not analysis:

```python
# Your guardrail, model, or validator decides this is an injection attempt:
raise PromptInjectionError("...")

# From here the SDK takes over: classify HARD_KILL -> abort un-catchably
# -> redact -> write the envelope to the DLQ -> escalate at L4.
```

**The SDK does not detect prompt injection, jailbreaks, hallucination, or
planning failures.** It contains no scanners, heuristics, or model calls for
them, and it cannot notice a failure that never raises (a plausible-looking wrong
answer, or a loop of individually-valid steps — for loops and runaway cost, use
`BudgetWatchdog`, which *does* enforce ceilings).

Detection is your model's, guardrail's, or validation layer's job — NeMo
Guardrails, Bedrock Guardrails, a Pydantic validator, your own check.
`PromptInjectionError` and friends are provided as first-class error types so
that hand-off boundary is explicit and typed, **not** because the SDK finds
injections. Once something raises, the SDK decides what the failure means, what
happens next, and what gets recorded.

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
  PANs, bidi controls) over exception **messages** and over **string values in
  context snapshots**. Snapshot handling redacts values under sensitively-named
  keys (`password`, `token`, `secret`, …) outright, and pattern-scans every
  other string value — including nested and list elements — so a secret embedded
  in an otherwise-benign field (`{"note": "token is sk-…"}`) is still redacted,
  not merely length-bounded. A single ReDoS budget spans the walk and fails
  closed. Redaction remains best-effort — see **Limitations & Scope**.
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
  unsigned, non-durable checkpoints. `attestation()` reports the active
  provider's capabilities:

  ```python
  from agentic_exception_sdk import attestation

  attestation()  # {'durable': False, 'checkpoint_signing': False, 'worm': False}
  ```

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

- **The SDK detects nothing; it routes what you raise.** Classification is type dispatch over exceptions your code raises — it is not detection. The SDK cannot see a prompt injection, a hallucination, or a wrong-but-plausible answer that never raises. Pair it with a real detection layer; see [What the classifier does and does not do](#what-the-classifier-does-and-does-not-do).
- **Redaction is best-effort.** The trust-boundary sanitizer is pattern-based; novel, split, or obfuscated secrets can pass through. It is not a substitute for a dedicated DLP control, and should not be your sole barrier against secret exposure.
- **`HARD_KILL` is in-execution termination, not an operator kill switch.** `AgentHardKillError` stops the agent's *own run* when a tripwire fires. It is **not** an out-of-band control that deactivates a running agent, agent type, or fleet on demand — that capability is on the roadmap. Do not represent it as a fleet-wide or human-pullable deactivation switch.
- **`AgentHardKillError` extends `BaseException` — mind your runtime.** This is deliberate (it survives `except Exception`), but runtimes that treat `BaseException` as fatal (Celery workers, some ASGI/thread-pool executors) may tear down the worker when it propagates. Catch it at your outermost executor boundary and translate it to your runtime's shutdown/abort convention; do not let it escape uncaught into a shared worker pool.
- **Integrity hashing is not a durable audit trail by default.** The in-process provider emits unsigned, non-durable checkpoints and attests this via `attestation()` (`durable=False`). Durable, signed, WORM-retained audit is delivered by the enterprise substrate on the roadmap.
- **Not a compliance certification.** Any coverage mapping (e.g. to the OWASP LLM Top 10) documents what the SDK *does*; it does not make your system compliant with any regulation. Compliance is a property of your overall system and processes.
- **No warranty.** Provided under the MIT License, **"AS IS," without warranty of any kind** (see [LICENSE](LICENSE)). You are responsible for validating its behavior in your own environment.

---

## License

MIT License — see [LICENSE](LICENSE). The SDK is provided "AS IS", without warranty of any kind; the authors are not liable for any claim or damages arising from its use. See **Limitations & Scope** above.
