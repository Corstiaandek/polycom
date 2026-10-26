from __future__ import annotations

import logging
from typing import Any

import sounddevice as sd

log = logging.getLogger(__name__)


def list_devices() -> list[dict[str, Any]]:
    devices = sd.query_devices()
    # sounddevice returns a DeviceList that behaves like a list of dicts
    return [dict(d) | {"index": i} for i, d in enumerate(devices)]


def find_device(match: str, *, kind: str) -> int | None:
    """Return device index matching substring for input or output."""
    needle = match.lower()
    best: int | None = None
    for i, d in enumerate(sd.query_devices()):
        name = str(d.get("name", "")).lower()
        if needle not in name:
            continue
        if kind == "output" and int(d.get("max_output_channels", 0)) <= 0:
            continue
        if kind == "input" and int(d.get("max_input_channels", 0)) <= 0:
            continue
        # Prefer WASAPI / DirectSound over WDM-KS when available
        host = ""
        try:
            hostapis = sd.query_hostapis()
            host = str(hostapis[d["hostapi"]]["name"])
        except Exception:
            pass
        if best is None:
            best = i
        elif "WASAPI" in host or "DirectSound" in host:
            best = i
            if "WASAPI" in host:
                break
    if best is not None:
        log.info("Matched %s device %s → index %s", kind, match, best)
    else:
        log.warning("No %s device matching %r", kind, match)
    return best


def describe_cx300(match: str = "Polycom CX300") -> str:
    lines: list[str] = []
    for d in list_devices():
        if match.lower() in str(d.get("name", "")).lower():
            lines.append(
                f"[{d['index']}] {d['name']} in={d.get('max_input_channels')} "
                f"out={d.get('max_output_channels')}"
            )
    return "\n".join(lines) if lines else f"(no devices matching {match!r})"
