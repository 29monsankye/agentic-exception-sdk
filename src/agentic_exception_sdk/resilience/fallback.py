"""Fallback chain protocol and implementations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

_log = logging.getLogger(__name__)

T = TypeVar("T")

__all__ = [
    "FallbackChain",
    "NoOpFallback",
    "OrderedFallbackChain",
]


@runtime_checkable
class FallbackChain(Protocol):
    """Ordered list of fallback callables tried in sequence until one succeeds."""

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the fallback chain.

        Args:
            *args: Positional arguments forwarded to each callable.
            **kwargs: Keyword arguments forwarded to each callable.

        Returns:
            The return value of the first callable that succeeds.
        """
        ...


class NoOpFallback:
    """Fallback chain that returns None without invoking any callable."""

    def execute(self, *args: Any, **kwargs: Any) -> None:
        """Return None immediately.

        Args:
            *args: Ignored.
            **kwargs: Ignored.

        Returns:
            None.
        """
        return None


class OrderedFallbackChain:
    """Fallback chain that tries each callable in registration order.

    On failure of one callable, the next is tried. If all callables fail,
    the last exception is re-raised.

    Args:
        callables: Ordered list of callables to try as fallbacks.
    """

    def __init__(self, callables: list[Callable[..., Any]]) -> None:
        self._callables = callables

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Execute fallbacks in order, returning the first success.

        Args:
            *args: Positional arguments forwarded to each callable.
            **kwargs: Keyword arguments forwarded to each callable.

        Returns:
            The return value of the first callable that succeeds.

        Raises:
            Exception: Re-raises the exception from the last callable if all fail.
        """
        last_exc: Exception | None = None
        for fn in self._callables:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                _log.debug("fallback callable %s failed: %s", fn, type(exc).__name__)
        if last_exc is not None:
            raise last_exc
        return None
