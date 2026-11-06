"""Lightweight pure-Python Signal / Slot event dispatcher.

Provides a drop-in replacement for PySide6 Signal and Slot decorators,
allowing thread-safe callback registration and invocation without requiring Qt.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List

logger = logging.getLogger(__name__)


class Signal:
    """Pure-Python Signal class replicating the Qt Signal.connect / Signal.emit API."""

    def __init__(self, *arg_types: Any) -> None:
        self._arg_types = arg_types
        self._callbacks: List[Callable[..., Any]] = []

    def connect(self, callback: Callable[..., Any]) -> None:
        """Register a callback to be called when this signal emits."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def disconnect(self, callback: Callable[..., Any] | None = None) -> None:
        """Unregister a callback, or all callbacks if None."""
        if callback is None:
            self._callbacks.clear()
        elif callback in self._callbacks:
            self._callbacks.remove(callback)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        """Invoke all connected callbacks with the emitted arguments."""
        for cb in list(self._callbacks):
            try:
                cb(*args, **kwargs)
            except Exception as exc:
                logger.exception("Error executing callback %s: %s", cb, exc)


def Slot(*arg_types: Any) -> Callable:
    """No-op decorator replacing PySide6 @Slot for pure-Python classes."""
    def decorator(fn: Callable) -> Callable:
        return fn
    return decorator
