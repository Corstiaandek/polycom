from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class DoneWatch:
    """
    Simple idle-after-send heuristic.

    After a prompt is submitted we wait `idle_seconds`, then fire `on_done`.
    Cancelled if a new recording starts or stop() is called.
    """

    def __init__(self, on_done: Callable[[], None]) -> None:
        self._on_done = on_done
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._generation = 0

    def arm(self, idle_seconds: float) -> None:
        with self._lock:
            self._cancel_locked()
            self._generation += 1
            gen = self._generation
            delay = max(1.0, float(idle_seconds))

            def fire() -> None:
                with self._lock:
                    if gen != self._generation:
                        return
                log.info("DoneWatch fired after %.1fs", delay)
                try:
                    self._on_done()
                except Exception:
                    log.exception("on_done failed")

            self._timer = threading.Timer(delay, fire)
            self._timer.daemon = True
            self._timer.start()
            log.info("DoneWatch armed for %.1fs", delay)

    def cancel(self) -> None:
        with self._lock:
            self._cancel_locked()
            self._generation += 1

    def _cancel_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
