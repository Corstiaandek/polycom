from __future__ import annotations

import argparse
import logging
import sys
import time

from .audio_devices import describe_cx300, list_devices
from .hid_cx300 import Cx300Device, LedColor, enumerate_cx300
from .ringer import generate_ringtone_wav


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CX300 HID/audio smoke test")
    parser.add_argument("--listen-seconds", type=float, default=15.0)
    parser.add_argument("--skip-display", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("=== Audio devices (Polycom) ===")
    print(describe_cx300())
    print()
    print("=== All devices (brief) ===")
    for d in list_devices():
        name = d.get("name", "")
        if "polycom" in name.lower() or "cx300" in name.lower():
            print(f"  [{d['index']}] {name}")

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    wav = root / "assets" / "ringtone.wav"
    generate_ringtone_wav(wav)
    print(f"Ringtone ready: {wav}")

    print()
    print("=== HID enumerate 095D:9201 ===")
    devices = enumerate_cx300()
    if not devices:
        print("No CX300 HID interfaces found. Plug in the phone and retry.")
        print("Audio/ringtone smoke OK; HID pending device.")
        return 2

    for d in devices:
        print(
            f"  path={d.get('path')!r} usage_page={d.get('usage_page')} "
            f"usage={d.get('usage')} interface={d.get('interface_number')}"
        )

    dev = Cx300Device()
    if not dev.open():
        print("Failed to open CX300")
        return 1

    if not args.skip_display:
        print("Writing test display + green LED...")
        try:
            dev.write_two_lines("CX300 OK", "Smoke test", max_chars=16)
            dev.set_led(LedColor.GREEN)
            print("Display write attempted (check phone LCD).")
        except OSError as exc:
            print(f"Display write failed: {exc}")

    print(f"Listening for hook/keys for {args.listen_seconds:.0f}s...")
    print("Pick up / hang up / press 1, 2, *")
    deadline = time.time() + args.listen_seconds
    last_hook = None
    while time.time() < deadline:
        data = dev.read_report(timeout_ms=100)
        if not data:
            continue
        if data[0] == 0x01 and len(data) >= 3:
            b1, b2 = data[1], data[2]
        else:
            b1, b2 = data[0], data[1] if len(data) > 1 else 0
        hook = bool(b1 & 0x01)
        if hook != last_hook:
            last_hook = hook
            print(f"  hook={'OFF' if hook else 'ON'} raw={data[:8].hex(' ')}")
        keypad = b2 & 0x0F
        if keypad:
            print(f"  keypad={keypad:#x} raw={data[:8].hex(' ')}")

    dev.close()
    print("Smoke done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
