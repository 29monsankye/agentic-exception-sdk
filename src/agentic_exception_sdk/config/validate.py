"""Startup validation for ResilienceBundle configuration."""

from __future__ import annotations

from agentic_exception_sdk.bundle import ResilienceBundle

__all__ = [
    "BundleValidationError",
    "validate_bundle",
]


class BundleValidationError(ValueError):
    """Raised when validate_bundle finds one or more configuration failures."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = list(failures)
        self.report = "\n".join(self.failures)
        super().__init__(self.report)


def validate_bundle(bundle: ResilienceBundle) -> None:
    """Validate a bundle at startup without mutating it.

    Args:
        bundle: The configured ResilienceBundle to inspect.

    Raises:
        BundleValidationError: If one or more configuration failures are found.
    """
    failures: list[str] = []
    _check_redis_circuit_breaker(bundle, failures)
    _check_guard_rails(bundle, failures)
    _check_budget(bundle, failures)
    if failures:
        raise BundleValidationError(failures)


def _check_redis_circuit_breaker(
    bundle: ResilienceBundle,
    failures: list[str],
) -> None:
    redis_url = getattr(bundle.circuit_breaker, "redis_url", None)
    if redis_url is None:
        return
    if not isinstance(redis_url, str) or not redis_url.startswith("rediss://"):
        failures.append("RedisCircuitBreaker: URL must use rediss:// (TLS required)")


def _check_guard_rails(bundle: ResilienceBundle, failures: list[str]) -> None:
    guard_rails = bundle.guard_rails
    allowlist = getattr(guard_rails, "allowlist", None)
    if allowlist is not None and len(allowlist) == 0:
        failures.append("GuardRailPolicy: allowlist is empty - all tool calls will be blocked")
        return

    allowlisted_operations = getattr(guard_rails, "_allowlisted_operations", None)
    canonical = getattr(allowlisted_operations, "canonical", None)
    if canonical is not None and len(canonical) == 0:
        failures.append("GuardRailPolicy: allowlist is empty - all tool calls will be blocked")


def _check_budget(bundle: ResilienceBundle, failures: list[str]) -> None:
    budget = getattr(bundle.agent_budget, "budget", None)
    if budget is None:
        budget = getattr(bundle.agent_budget, "_budget", None)
    if budget is None:
        return

    max_cost = getattr(budget, "max_cost_micros", None)
    if max_cost is None:
        max_cost = getattr(budget, "max_total_cost_micros_usd", None)
    if max_cost is not None and max_cost <= 0:
        failures.append("AgentBudget: max_cost_micros must be positive")
