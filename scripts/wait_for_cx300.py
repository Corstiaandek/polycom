"""Poll until Polycom CX300 HID appears, then exit 0."""

from __future__ import annotations

import sys
import time

import hid

VID, PID = 0x095D, 0x9201


def main() -> int:
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    print(f"Waiting up to {timeout:.0f}s for CX300 USB (095D:9201)...")
    print("Plug the phone into a USB port on this PC now.")
    deadline = time.time() + timeout
    while time.time() < deadline:
        n = len(hid.enumerate(VID, PID))
        if n:
            print(f"FOUND {n} HID interface(s)")
            return 0
        time.sleep(1.0)
        left = int(deadline - time.time())
        if left % 10 == 0:
            print(f"  still waiting... {left}s left")
    print("TIMEOUT: CX300 not detected")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
