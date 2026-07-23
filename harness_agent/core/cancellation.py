"""Cooperative task cancellation shared by Web orchestration and subprocesses."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class TaskCancelled(RuntimeError):
    """Raised when a user stops an active task."""


class CancellationToken:
    """Thread-safe cancellation state with immediate subprocess terminators."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._terminators: dict[int, Callable[[], Any]] = {}
        self._next_registration = 0

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            terminators = list(self._terminators.values())
        for terminate in terminators:
            try:
                terminate()
            except Exception:
                continue

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled("task stopped by user")

    def register_terminator(self, terminate: Callable[[], Any]) -> int:
        with self._lock:
            self._next_registration += 1
            registration = self._next_registration
            self._terminators[registration] = terminate
            cancelled = self.cancelled
        if cancelled:
            terminate()
        return registration

    def unregister_terminator(self, registration: int | None) -> None:
        if registration is None:
            return
        with self._lock:
            self._terminators.pop(registration, None)
