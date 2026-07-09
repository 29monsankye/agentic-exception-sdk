"""Guard rail policy: enforces tool call allowlist and operation constraints.

Guard-rail violations are always HARD_KILL because continuing would violate a
host-defined policy boundary. AllowlistedOperations must never construct its
own TrustBoundaryValidator internally.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agentic_exception_sdk.taxonomy.errors import GuardRailViolationError
from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator

__all__ = [
    "AllowlistedOperations",
    "GuardRailPolicy",
    "NoOpGuardRails",
]


@dataclass(frozen=True)
class AllowlistedOperations:
    """Set of canonical operation names approved by the host project.

    Names are canonicalized at construction time using the injected
    TrustBoundaryValidator. AllowlistedOperations must NEVER construct its own
    TrustBoundaryValidator internally.

    Args:
        canonical: frozenset of pre-canonicalized operation name strings.
    """

    canonical: frozenset[str]

    @classmethod
    def from_iterable(
        cls,
        operations: Iterable[str],
        *,
        trust_boundary: TrustBoundaryValidator,
    ) -> AllowlistedOperations:
        """Build AllowlistedOperations by canonicalizing each name with the injected validator.

        Host input such as ``"Search_Flights"`` and ``"process_payment "`` will be
        normalized to ``"search_flights"`` and ``"process_payment"`` so they match
        canonical tool call names without causing false HARD_KILL failures.

        Args:
            operations: Raw operation names from the host project configuration.
            trust_boundary: The injected TrustBoundaryValidator. Never construct internally.

        Returns:
            AllowlistedOperations with all names pre-canonicalized.
        """
        return cls(
            canonical=frozenset(
                trust_boundary.canonicalize_tool_name(op) for op in operations
            )
        )


class NoOpGuardRails:
    """Guard rails that allow all operations without restriction."""

    def check(self, tool_name: str) -> None:
        """Allow any tool name without checking.

        Args:
            tool_name: Ignored.
        """
        return None


class GuardRailPolicy:
    """Enforces tool call allowlist using canonicalized names.

    The policy canonicalizes the incoming tool_name before checking the allowlist,
    using the same injected TrustBoundaryValidator used to build AllowlistedOperations.
    This ensures that homoglyph tricks, casing differences, and whitespace cannot
    bypass the allowlist.

    Args:
        trust_boundary: Injected validator for canonical name normalization.
        allowlisted_operations: Pre-canonicalized operation allowlist.
    """

    def __init__(
        self,
        *,
        trust_boundary: TrustBoundaryValidator,
        allowlisted_operations: AllowlistedOperations,
    ) -> None:
        self._trust_boundary = trust_boundary
        self._allowlisted_operations = allowlisted_operations

    def check(self, tool_name: str) -> None:
        """Check whether the tool_name is in the allowlist.

        The tool_name is canonicalized using the injected TrustBoundaryValidator
        before the allowlist check. This method must be called with the already-
        canonical name from resilient()'s canonicalization step; double-calling
        canonicalize_tool_name is idempotent.

        Args:
            tool_name: The tool name to check (may already be canonical).

        Raises:
            GuardRailViolationError: If the canonical tool name is not in the allowlist.
        """
        canonical = self._trust_boundary.canonicalize_tool_name(tool_name)
        if canonical not in self._allowlisted_operations.canonical:
            raise GuardRailViolationError(canonical)
