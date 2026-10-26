from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

import hid

log = logging.getLogger(__name__)

VENDOR_ID = 0x095D
PRODUCT_ID = 0x9201

# Keypad second-byte (report bytes 1-2 after report id) from OpenPhone/bsmulders docs.
KEYPAD_CODES: dict[int, str] = {
    0x01: "0",
    0x02: "1",
    0x03: "2",
    0x04: "3",
    0x05: "4",
    0x06: "5",
    0x07: "6",
    0x08: "7",
    0x09: "8",
    0x0A: "9",
    0x0B: "*",
    0x0C: "#",
}


class LedColor(Enum):
    GREEN = auto()
    RED = auto()
    YELLOW = auto()
    YELLOW_RED = auto()
    OFF = auto()


LED_BYTES: dict[LedColor, bytes] = {
    LedColor.GREEN: bytes([0x16, 0x01]),
    LedColor.RED: bytes([0x16, 0x03]),
    LedColor.YELLOW_RED: bytes([0x16, 0x04]),
    LedColor.YELLOW: bytes([0x16, 0x05]),
    LedColor.OFF: bytes([0x16, 0x07]),
}


@dataclass(frozen=True)
class Cx300Event:
    kind: str  # hook_off | hook_on | key | mute | raw
    key: str | None = None
    muted: bool | None = None
    raw: bytes | None = None


def enumerate_cx300() -> list[dict[str, Any]]:
    return [d for d in hid.enumerate(VENDOR_ID, PRODUCT_ID)]


class Cx300Device:
    """HID open/read/write for Polycom CX300 (VID 095D / PID 9201)."""

    def __init__(self) -> None:
        self._dev: hid.device | None = None
        self._write_dev: hid.device | None = None
        self._lock = threading.Lock()
        self._initialized_display = False

    @property
    def connected(self) -> bool:
        return self._dev is not None

    def open(self) -> bool:
        self.close()
        devices = enumerate_cx300()
        if not devices:
            return False

        # Prefer telephony usage page 0x0B when available; otherwise first path.
        primary = next(
            (d for d in devices if d.get("usage_page") == 0x0B),
            devices[0],
        )
        path = primary["path"]
        try:
            self._dev = hid.device()
            self._dev.open_path(path)
            self._dev.set_nonblocking(True)
        except OSError as exc:
            log.warning("Failed to open CX300 HID path %s: %s", path, exc)
            self._dev = None
            return False

        # tSIP note: Windows sometimes needs a second open for display writes.
        self._write_dev = self._dev
        if len(devices) > 1:
            alt = next((d for d in devices if d["path"] != path), None)
            if alt is not None:
                try:
                    w = hid.device()
                    w.open_path(alt["path"])
                    w.set_nonblocking(True)
                    self._write_dev = w
                    log.info("Opened secondary HID interface for display/LED writes")
                except OSError as exc:
                    log.debug("Secondary HID open failed (using primary): %s", exc)

        log.info(
            "Opened CX300: %s / %s",
            self._safe_str(self._dev.get_manufacturer_string()),
            self._safe_str(self._dev.get_product_string()),
        )
        return True

    @staticmethod
    def _safe_str(value: str | None) -> str:
        try:
            return value or "?"
        except Exception:
            return "?"

    def close(self) -> None:
        with self._lock:
            for attr in ("_write_dev", "_dev"):
                dev = getattr(self, attr)
                if dev is None:
                    continue
                try:
                    if attr == "_write_dev" and self._write_dev is self._dev:
                        continue
                    dev.close()
                except Exception:
                    pass
            self._dev = None
            self._write_dev = None
            self._initialized_display = False

    def _write(self, data: bytes | list[int]) -> None:
        payload = bytes(data)
        with self._lock:
            if self._write_dev is None:
                raise RuntimeError("CX300 not connected")
            # hidapi on Windows expects report id as first byte of buffer.
            self._write_dev.write(payload)
        time.sleep(0.03)

    def init_display_session(self) -> None:
        """Send LCID feature report once per connection (English)."""
        with self._lock:
            if self._write_dev is None:
                return
            if self._initialized_display:
                return
            try:
                # Feature report id 0x17, LCID English 0x09...
                self._write_dev.send_feature_report([0x17, 0x09, 0x04, 0x01, 0x02])
                self._initialized_display = True
                time.sleep(0.05)
            except OSError as exc:
                log.warning("Feature report init failed: %s", exc)

    def set_led(self, color: LedColor) -> None:
        self._write(LED_BYTES[color])

    def clear_display(self) -> None:
        self._write([0x13, 0x00])

    def set_two_line_mode(self) -> None:
        self._write([0x13, 0x15])

    def select_line(self, line: int) -> None:
        # Top=0x05, Bottom=0x0A
        code = 0x05 if line == 1 else 0x0A
        self._write([0x14, code, 0x80])

    def write_utf16_chunk(self, text_bytes: bytes, final: bool) -> None:
        flag = 0x80 if final else 0x00
        # Report: 0x15, flag, then up to 8 UTF-16LE code units (16 bytes)
        chunk = text_bytes[:16]
        if len(chunk) < 16:
            chunk = chunk + bytes(16 - len(chunk))
        self._write(bytes([0x15, flag]) + chunk)

    def write_line_text(self, line: int, text: str, max_chars: int = 16) -> None:
        """Write one display line (UTF-16LE chunks)."""
        self.init_display_session()
        clipped = (text or "")[:max_chars]
        encoded = clipped.encode("utf-16le")
        self.select_line(line)
        # 8 UTF-16 units per packet (16 bytes payload)
        if not encoded:
            self.write_utf16_chunk(b"", final=True)
            return
        offset = 0
        while offset < len(encoded):
            piece = encoded[offset : offset + 16]
            offset += 16
            final = offset >= len(encoded)
            self.write_utf16_chunk(piece, final=final)

    def write_two_lines(self, line1: str, line2: str, max_chars: int = 16) -> None:
        try:
            self.init_display_session()
            self.clear_display()
            self.set_two_line_mode()
            self.write_line_text(1, line1, max_chars=max_chars)
            self.write_line_text(2, line2, max_chars=max_chars)
        except OSError as exc:
            log.warning("Display write failed: %s", exc)

    def read_report(self, timeout_ms: int = 50) -> bytes | None:
        if self._dev is None:
            return None
        try:
            data = self._dev.read(64, timeout_ms)
        except OSError as exc:
            log.warning("HID read failed: %s", exc)
            self.close()
            return None
        if not data:
            return None
        return bytes(data)

    def parse_report(self, data: bytes) -> list[Cx300Event]:
        """Parse telephony input report into high-level events."""
        events: list[Cx300Event] = []
        if len(data) < 3:
            return events

        # Reports may or may not include report id 0x01 as first byte.
        if data[0] == 0x01 and len(data) >= 4:
            b1, b2 = data[1], data[2]
            mute_byte = data[7] if len(data) > 7 else None
        else:
            b1, b2 = data[0], data[1]
            mute_byte = data[6] if len(data) > 6 else None

        # Hook: bit in first key byte (HOOK_UP = 01 00)
        if b1 & 0x01:
            events.append(Cx300Event(kind="hook_off", raw=data))
        else:
            # Only emit hook_on when no other primary key in b1 telephony bits
            # and keypad is empty — caller tracks edge transitions.
            events.append(Cx300Event(kind="hook_on", raw=data))

        keypad = b2 & 0x0F
        if keypad in KEYPAD_CODES:
            events.append(Cx300Event(kind="key", key=KEYPAD_CODES[keypad], raw=data))

        # Extra button bits in b1
        if b1 & 0x10:
            events.append(Cx300Event(kind="mute", muted=True, raw=data))
        if b1 & 0x02:
            events.append(Cx300Event(kind="key", key="flash", raw=data))
        if b1 & 0x04:
            events.append(Cx300Event(kind="key", key="redial", raw=data))
        if b1 & 0x20:
            events.append(Cx300Event(kind="key", key="delete", raw=data))

        if mute_byte is not None:
            events.append(Cx300Event(kind="mute", muted=bool(mute_byte & 0x01), raw=data))

        return events


class Cx300Listener:
    """Background thread that edges hook/key events to callbacks."""

    def __init__(
        self,
        on_event: Callable[[Cx300Event], None],
        reconnect_seconds: float = 2.0,
    ) -> None:
        self._on_event = on_event
        self._reconnect_seconds = reconnect_seconds
        self._device = Cx300Device()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hook_off = False
        self._last_key: str | None = None
        self._was_connected = False

    @property
    def device(self) -> Cx300Device:
        return self._device

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cx300-hid", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._device.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._device.connected:
                if self._device.open():
                    self._was_connected = True
                    self._hook_off = False
                    self._last_key = None
                    self._on_event(Cx300Event(kind="connected"))
                else:
                    if self._was_connected:
                        self._was_connected = False
                        self._on_event(Cx300Event(kind="disconnected"))
                    self._stop.wait(self._reconnect_seconds)
                    continue

            data = self._device.read_report(timeout_ms=100)
            if data is None:
                if not self._device.connected:
                    continue
                continue

            # Edge-detect hook and keys from raw bytes
            if data[0] == 0x01 and len(data) >= 3:
                b1, b2 = data[1], data[2]
            else:
                b1, b2 = data[0], data[1] if len(data) > 1 else 0

            hook_off = bool(b1 & 0x01)
            if hook_off and not self._hook_off:
                self._hook_off = True
                self._on_event(Cx300Event(kind="hook_off", raw=data))
            elif not hook_off and self._hook_off:
                self._hook_off = False
                self._on_event(Cx300Event(kind="hook_on", raw=data))

            keypad = b2 & 0x0F
            key = KEYPAD_CODES.get(keypad)
            if key and key != self._last_key:
                self._last_key = key
                self._on_event(Cx300Event(kind="key", key=key, raw=data))
            elif keypad == 0:
                self._last_key = None
