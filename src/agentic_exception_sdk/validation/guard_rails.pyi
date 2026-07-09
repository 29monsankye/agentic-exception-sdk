from agentic_exception_sdk.validation.trust_boundary import TrustBoundaryValidator
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ['AllowlistedOperations', 'GuardRailPolicy', 'NoOpGuardRails']

@dataclass(frozen=True)
class AllowlistedOperations:
    canonical: frozenset[str]
    @classmethod
    def from_iterable(cls, operations: Iterable[str], *, trust_boundary: TrustBoundaryValidator) -> AllowlistedOperations: ...

class NoOpGuardRails:
    def check(self, tool_name: str) -> None: ...

class GuardRailPolicy:
    def __init__(self, *, trust_boundary: TrustBoundaryValidator, allowlisted_operations: AllowlistedOperations) -> None: ...
    def check(self, tool_name: str) -> None: ...
