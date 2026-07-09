"""Output validation gate protocol and implementations."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from agentic_exception_sdk.taxonomy.errors import ValidationGateError

T = TypeVar("T")

__all__ = [
    "NoOpGate",
    "OutputValidationGate",
    "PydanticValidationGate",
]


@runtime_checkable
class OutputValidationGate(Protocol):
    """Validates a tool's output against a schema before it is returned to the caller.

    Implementations must not include raw input data in any raised error messages
    because ValidationGateError is logged and may appear in telemetry.
    """

    def validate(self, result: Any) -> Any:
        """Validate the tool output.

        Args:
            result: The raw output from the tool callable.

        Returns:
            The validated output (may be a parsed/coerced version of result).

        Raises:
            ValidationGateError: If the output does not conform to the expected schema.
        """
        ...


class NoOpGate:
    """Pass-through validation gate — always returns result unchanged."""

    def validate(self, result: Any) -> Any:
        """Return result without any validation.

        Args:
            result: Any value.

        Returns:
            The same value unchanged.
        """
        return result


class PydanticValidationGate:
    """Validates a tool output against a Pydantic v2 model schema.

    On validation failure, raises ValidationGateError with a safe message that
    does not include raw input data (to prevent leaking sensitive content into
    logs or telemetry).

    Args:
        schema: A Pydantic BaseModel subclass to validate the output against.
    """

    def __init__(self, schema: type[BaseModel]) -> None:
        self._schema = schema

    def validate(self, result: Any) -> Any:
        """Validate result against the configured Pydantic schema.

        Args:
            result: The raw output from the tool callable.

        Returns:
            The validated Pydantic model instance.

        Raises:
            ValidationGateError: If result does not conform to the schema.
                The error message includes only the error count, not raw input data.
        """
        try:
            return self._schema.model_validate(result)
        except PydanticValidationError as exc:
            # Never include exc.errors() or raw input data in the message
            raise ValidationGateError(
                f"output validation failed: {len(exc.errors())} error(s) "
                f"against schema {self._schema.__name__!r}"
            ) from None
