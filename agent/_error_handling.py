"""Error-handling decorator for system boundaries.

Use this ONLY at system boundaries (LLM calls, Tavily calls, Redis calls)
where transient failures are expected. Pure functions and business logic
should let exceptions bubble up so bugs surface loudly.

Usage:
    @with_logged_errors(default_return=None, op_name="tavily_search")
    def search(self, query):
        ...
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def with_logged_errors(
    default_return: Any = None,
    op_name: str | None = None,
) -> Callable[[F], F]:
    """Decorator: log exceptions with traceback and return ``default_return``.

    Use only at system boundaries. Pure functions and business logic
    should fail loudly.
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                name = op_name or func.__qualname__
                logger.exception(f"Error in {name}: {type(e).__name__}: {e}")
                return default_return
        return wrapper  # type: ignore[return-value]
    return decorator
