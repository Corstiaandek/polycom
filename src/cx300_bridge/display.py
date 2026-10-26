from __future__ import annotations

import logging
from datetime import datetime

from .hid_cx300 import Cx300Device, LedColor

log = logging.getLogger(__name__)


class DisplayController:
    """Map app state → LCD two-line text + presence LED."""

    def __init__(self, device: Cx300Device, line_max: int = 16) -> None:
        self._device = device
        self._line_max = line_max
        self._last: tuple[str, str, LedColor] | None = None

    def show(self, line1: str, line2: str, led: LedColor) -> None:
        if not self._device.connected:
            return
        key = (line1[: self._line_max], line2[: self._line_max], led)
        if key == self._last:
            return
        self._last = key
        try:
            self._device.write_two_lines(key[0], key[1], max_chars=self._line_max)
            self._device.set_led(led)
        except Exception as exc:
            log.warning("Display update failed: %s", exc)

    def idle(self, target_label: str, *, with_clock: bool = False) -> None:
        line2 = datetime.now().strftime("%H:%M") if with_clock else "Pak hoorn op"
        self.show(target_label, line2, LedColor.GREEN)

    def target_switched(self, target_label: str) -> None:
        self.show(target_label, "Gereed", LedColor.GREEN)

    def recording(self, target_label: str) -> None:
        self.show(target_label, "Opnemen...", LedColor.YELLOW)

    def waiting(self, target_label: str) -> None:
        self.show(target_label, "Bezig...", LedColor.YELLOW_RED)

    def ringing(self, target_label: str) -> None:
        self.show(target_label, "Klaar", LedColor.RED)

    def error(self, target_label: str, message: str) -> None:
        self.show(target_label, message[: self._line_max], LedColor.RED)

    def paused(self) -> None:
        self.show("Pauze", "Tray: Resume", LedColor.OFF)

    def invalidate(self) -> None:
        self._last = None
