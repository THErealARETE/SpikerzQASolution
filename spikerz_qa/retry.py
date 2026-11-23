"""Bounded retry with exponential backoff for transient provider failures."""
from __future__ import annotations

import time
from typing import Callable, TypeVar

from .providers import TransientProviderError

T = TypeVar("T")


def with_retry(fn: Callable[[], T], attempts: int = 3, base_delay: float = 1.0,
               sleep: Callable[[float], None] = time.sleep) -> tuple[T, int, list[str]]:
    """Return (result, attempts_used, transient_errors). Re-raises after the last attempt."""
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            return fn(), attempt, errors
        except TransientProviderError as exc:
            errors.append(f"attempt {attempt}: {exc}")
            if attempt == attempts:
                raise TransientProviderError("; ".join(errors)) from exc
            sleep(base_delay * (2 ** (attempt - 1)))
    raise AssertionError("unreachable")
