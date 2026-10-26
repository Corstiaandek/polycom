from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)


def _make_icon(color: tuple[int, int, int], size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 8
    draw.ellipse([margin, margin, size - margin, size - margin], fill=color + (255,))
    # Handset-ish mark
    draw.rectangle(
        [size // 2 - 4, size // 3, size // 2 + 4, 2 * size // 3],
        fill=(255, 255, 255, 230),
    )
    return img


ICON_COLORS = {
    "idle": (46, 160, 67),
    "recording": (210, 160, 30),
    "waiting": (210, 120, 30),
    "ringing": (200, 50, 50),
    "paused": (120, 120, 120),
    "error": (180, 40, 40),
}


class TrayApp:
    def __init__(
        self,
        *,
        get_status: Callable[[], str],
        on_select_target: Callable[[str], None],
        target_labels: list[tuple[str, str]],
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
        on_test_ring: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._get_status = get_status
        self._on_select_target = on_select_target
        self._target_labels = target_labels
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._on_test_ring = on_test_ring
        self._on_quit = on_quit
        self._icon: Any = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            import pystray
            from pystray import Menu, MenuItem
        except ImportError:
            log.error("pystray not installed — tray UI disabled")
            return

        def make_target_item(tid: str, label: str) -> MenuItem:
            return MenuItem(label, lambda: self._on_select_target(tid))

        items = [make_target_item(tid, label) for tid, label in self._target_labels]
        menu = Menu(
            MenuItem("Targets", Menu(*items)),
            MenuItem("Pause", lambda: self._on_pause()),
            MenuItem("Resume", lambda: self._on_resume()),
            MenuItem("Test ring", lambda: self._on_test_ring()),
            MenuItem("Quit", lambda: self._request_quit()),
        )
        self._icon = pystray.Icon(
            "cx300_bridge",
            _make_icon(ICON_COLORS["idle"]),
            "CX300 | starting",
            menu,
        )
        self._thread = threading.Thread(target=self._icon.run, name="cx300-tray", daemon=True)
        self._thread.start()

    def _request_quit(self) -> None:
        try:
            self._on_quit()
        finally:
            self.stop()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass

    def set_visual(self, state_name: str, tooltip: str) -> None:
        if self._icon is None:
            return
        color = ICON_COLORS.get(state_name.lower(), ICON_COLORS["idle"])
        try:
            self._icon.icon = _make_icon(color)
            self._icon.title = tooltip
        except Exception as exc:
            log.debug("Tray visual update failed: %s", exc)
