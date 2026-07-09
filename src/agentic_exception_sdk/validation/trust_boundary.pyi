from collections.abc import Mapping
from typing import Any

from agentic_exception_sdk.taxonomy.envelope import SafeContextSnapshot

__all__ = ['RE2_AVAILABLE', 'TrustBoundaryValidator']

RE2_AVAILABLE: bool

class TrustBoundaryValidator:
    def __init__(self, metrics_collector: Any | None = None) -> None: ...
    def canonicalize_tool_name(self, tool_name: str) -> str: ...
    def safe_exception_message(self, exc: BaseException) -> str: ...
    def safe_identifier(self, value: object) -> str: ...
    def sanitize_context_snapshot(
        self,
        context_snapshot: Mapping[str, Any] | None,
    ) -> SafeContextSnapshot: ...
