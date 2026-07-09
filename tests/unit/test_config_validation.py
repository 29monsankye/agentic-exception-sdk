from __future__ import annotations

import pytest

from agentic_exception_sdk import (
    AgentBudget,
    AllowlistedOperations,
    BudgetWatchdog,
    BundleValidationError,
    GuardRailPolicy,
    ResilienceBundle,
    TrustBoundaryValidator,
    validate_bundle,
)


class _RedisLikeCircuitBreaker:
    redis_url = "redis://:pw@example.com:6379/0"


def test_validate_bundle_accepts_default_bundle() -> None:
    validate_bundle(ResilienceBundle())


def test_validate_bundle_collects_all_failures() -> None:
    trust_boundary = TrustBoundaryValidator()
    empty_allowlist = AllowlistedOperations.from_iterable(
        [],
        trust_boundary=trust_boundary,
    )
    bundle = ResilienceBundle(
        circuit_breaker=_RedisLikeCircuitBreaker(),
        guard_rails=GuardRailPolicy(
            trust_boundary=trust_boundary,
            allowlisted_operations=empty_allowlist,
        ),
        agent_budget=BudgetWatchdog(
            AgentBudget(max_total_cost_micros_usd=0),
        ),
    )

    with pytest.raises(BundleValidationError) as exc_info:
        validate_bundle(bundle)

    assert exc_info.value.failures == [
        "RedisCircuitBreaker: URL must use rediss:// (TLS required)",
        "GuardRailPolicy: allowlist is empty - all tool calls will be blocked",
        "AgentBudget: max_cost_micros must be positive",
    ]
    assert exc_info.value.report == "\n".join(exc_info.value.failures)


def test_validate_bundle_does_not_modify_bundle_state() -> None:
    bundle = ResilienceBundle()
    original_bus = bundle.propagation_bus

    validate_bundle(bundle)

    assert bundle.propagation_bus is original_bus
