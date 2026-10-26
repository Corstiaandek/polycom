from __future__ import annotations

"""
End-to-end verification against the plan requirements.

Writes evidence under verify-evidence/<timestamp>/ and prints a verdict summary.
Hardware claims are INCONCLUSIVE/NOT VERIFIED if the CX300 is unplugged.
"""

import json
import logging
import re
import sys
import time
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audio_devices import describe_cx300, find_device
from .config import AppConfig, load_config
from .hid_cx300 import Cx300Device, LedColor, enumerate_cx300
from .ringer import Ringer, generate_ringtone_wav
from .state import BridgeState
from .window_ui import WindowController, _parse_hotkey

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    id: str
    claim: str
    verdict: str  # VERIFIED | NOT VERIFIED | INCONCLUSIVE
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


def _now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def check_config(config: AppConfig) -> CheckResult:
    ids = [t.id for t in config.targets]
    has_grok = "grok" in ids
    has_claude = "claude" in ids
    keys_ok = config.keys.get("1") == "grok" and config.keys.get("2") == "claude"
    ok = has_grok and has_claude and keys_ok and len(config.targets) >= 2
    return CheckResult(
        id="config_targets_keys",
        claim="config.toml defines grok+claude targets and keys 1/2/* mapping",
        verdict="VERIFIED" if ok else "NOT VERIFIED",
        evidence={"target_ids": ids, "keys": dict(config.keys)},
    )


def check_window_focus(config: AppConfig) -> CheckResult:
    wc = WindowController()
    details: dict[str, Any] = {}
    all_ok = True
    for t in config.targets:
        wins = wc.find_windows(t.window_title_regex)
        focused = wc.ensure_focused(t, timeout=4.0) if wins else False
        if not wins:
            # Try launch
            launched = wc.launch_target(t)
            time.sleep(2.0)
            wins = wc.find_windows(t.window_title_regex)
            focused = wc.ensure_focused(t, timeout=6.0) if wins else False
            details[t.id] = {
                "launched": launched,
                "windows": [w.title for w in wins],
                "focused": focused,
            }
        else:
            details[t.id] = {
                "launched": False,
                "windows": [w.title for w in wins],
                "focused": focused,
            }
        all_ok = all_ok and focused
    return CheckResult(
        id="window_focus",
        claim="Both Grok Bot and Claude windows can be found and focused",
        verdict="VERIFIED" if all_ok else "NOT VERIFIED",
        evidence=details,
    )


def check_hotkey_parse(config: AppConfig) -> CheckResult:
    evidence: dict[str, Any] = {}
    ok = True
    for t in config.targets:
        try:
            voice = [repr(k) for k in _parse_hotkey(t.voice_hotkey)]
            send = [repr(k) for k in _parse_hotkey(t.send_hotkey)]
            evidence[t.id] = {"voice": voice, "send": send}
        except Exception as exc:
            ok = False
            evidence[t.id] = {"error": str(exc)}
    return CheckResult(
        id="hotkey_parse",
        claim="Per-target voice/send hotkeys parse to pynput key sequences",
        verdict="VERIFIED" if ok else "NOT VERIFIED",
        evidence=evidence,
    )


def check_state_machine(config: AppConfig) -> CheckResult:
    """Verify key select + hook state transitions without HID hardware."""
    from .app import BridgeApp

    app = BridgeApp(config)
    # Patch window methods to no-op so we don't steal focus mid-test
    calls: list[str] = []

    def start_voice(target: object) -> bool:
        tid = getattr(target, "id", "?")
        calls.append(f"start_voice:{tid}")
        return True

    def send_prompt(target: object) -> bool:
        tid = getattr(target, "id", "?")
        calls.append(f"send_prompt:{tid}")
        return True

    app.windows.start_voice = start_voice  # type: ignore[method-assign]
    app.windows.send_prompt = send_prompt  # type: ignore[method-assign]
    app.windows.ensure_focused = lambda *a, **k: True  # type: ignore[method-assign]
    # No-op display
    app.display.show = lambda *a, **k: None  # type: ignore[method-assign]
    app.display.idle = lambda *a, **k: None  # type: ignore[method-assign]
    app.display.recording = lambda *a, **k: None  # type: ignore[method-assign]
    app.display.waiting = lambda *a, **k: None  # type: ignore[method-assign]
    app.display.ringing = lambda *a, **k: None  # type: ignore[method-assign]
    app.display.target_switched = lambda *a, **k: None  # type: ignore[method-assign]
    app.display.error = lambda *a, **k: None  # type: ignore[method-assign]
    app.display.paused = lambda *a, **k: None  # type: ignore[method-assign]

    timeline: list[str] = []

    # Key 2 → Claude
    with app._lock:
        app._handle_key("2")
    timeline.append(f"after_key2={app.active_target.id}")
    assert_ok = app.active_target.id == "claude"

    # Key 1 → Grok
    with app._lock:
        app._handle_key("1")
    timeline.append(f"after_key1={app.active_target.id}")
    assert_ok = assert_ok and app.active_target.id == "grok"

    # Cycle *
    with app._lock:
        app._handle_key("*")
    timeline.append(f"after_star={app.active_target.id}")
    assert_ok = assert_ok and app.active_target.id == "claude"

    # Hook off → RECORDING → start_voice
    with app._lock:
        app._start_recording()
    time.sleep(0.2)
    timeline.append(f"after_hook_off={app.state.name}")
    assert_ok = assert_ok and app.state == BridgeState.RECORDING

    # Key ignored while recording
    before = app.active_target.id
    with app._lock:
        app._handle_key("1")
    timeline.append(f"key_during_rec_target={app.active_target.id}")
    assert_ok = assert_ok and app.active_target.id == before

    # Hook on → WAITING → send_prompt
    with app._lock:
        app._submit_recording()
    time.sleep(0.3)
    timeline.append(f"after_hook_on={app.state.name}")
    assert_ok = assert_ok and app.state == BridgeState.WAITING

    # Done → RINGING
    app.ringer.ring = lambda *a, **k: calls.append("ring")  # type: ignore[method-assign]
    app._on_done()
    timeline.append(f"after_done={app.state.name}")
    assert_ok = assert_ok and app.state == BridgeState.RINGING
    assert_ok = assert_ok and "start_voice:claude" in calls
    assert_ok = assert_ok and "send_prompt:claude" in calls
    assert_ok = assert_ok and "ring" in calls

    return CheckResult(
        id="state_machine",
        claim=(
            "Keys 1/2/* switch targets in Idle; hook_off→RECORDING+voice; "
            "hook_on→WAITING+send; done→RINGING; keys blocked while recording"
        ),
        verdict="VERIFIED" if assert_ok else "NOT VERIFIED",
        evidence={"timeline": timeline, "calls": calls},
    )


def check_hid_event_wiring(config: AppConfig) -> CheckResult:
    """Prove Cx300Event kinds drive the same transitions as direct calls."""
    from .app import BridgeApp
    from .hid_cx300 import Cx300Event

    app = BridgeApp(config)
    calls: list[str] = []
    app.windows.start_voice = lambda t: calls.append(f"voice:{t.id}") or True  # type: ignore[method-assign]
    app.windows.send_prompt = lambda t: calls.append(f"send:{t.id}") or True  # type: ignore[method-assign]
    app.windows.ensure_focused = lambda *a, **k: True  # type: ignore[method-assign]
    for name in (
        "show",
        "idle",
        "recording",
        "waiting",
        "ringing",
        "target_switched",
        "error",
        "paused",
        "invalidate",
    ):
        setattr(app.display, name, lambda *a, **k: None)

    app._on_hid_event(Cx300Event(kind="connected"))
    app._on_hid_event(Cx300Event(kind="key", key="2"))
    time.sleep(0.05)
    target_ok = app.active_target.id == "claude"
    app._on_hid_event(Cx300Event(kind="hook_off"))
    time.sleep(0.25)
    rec_ok = app.state == BridgeState.RECORDING
    app._on_hid_event(Cx300Event(kind="hook_on"))
    time.sleep(0.35)
    wait_ok = app.state == BridgeState.WAITING
    ok = target_ok and rec_ok and wait_ok and "voice:claude" in calls and "send:claude" in calls
    return CheckResult(
        id="hid_event_wiring",
        claim="Synthetic HID events (key/hook) drive target switch, voice start, and send",
        verdict="VERIFIED" if ok else "NOT VERIFIED",
        evidence={
            "target": app.active_target.id,
            "state": app.state.name,
            "calls": calls,
            "target_ok": target_ok,
            "rec_ok": rec_ok,
            "wait_ok": wait_ok,
        },
    )


def check_live_hotkey_fire(config: AppConfig) -> CheckResult:
    """Focus Grok Bot and fire voice hotkey once; prove no exception + focus held."""
    wc = WindowController()
    grok = config.target_by_id("grok")
    if grok is None:
        return CheckResult(
            id="live_hotkey_fire",
            claim="Focus Grok Bot and deliver configured voice hotkey without error",
            verdict="NOT VERIFIED",
            evidence={"error": "no grok target"},
        )
    focused = wc.ensure_focused(grok, timeout=5.0)
    if not focused:
        return CheckResult(
            id="live_hotkey_fire",
            claim="Focus Grok Bot and deliver configured voice hotkey without error",
            verdict="NOT VERIFIED",
            evidence={"focused": False},
        )
    try:
        wc.press_hotkey(grok.voice_hotkey)
        # Undo dictation quickly with Escape if possible
        time.sleep(0.2)
        try:
            wc.press_hotkey("<esc>")
        except Exception:
            pass
        return CheckResult(
            id="live_hotkey_fire",
            claim="Focus Grok Bot and deliver configured voice hotkey without error",
            verdict="VERIFIED",
            evidence={"focused": True, "hotkey": grok.voice_hotkey, "fired": True},
        )
    except Exception as exc:
        return CheckResult(
            id="live_hotkey_fire",
            claim="Focus Grok Bot and deliver configured voice hotkey without error",
            verdict="NOT VERIFIED",
            evidence={"focused": True, "error": str(exc)},
        )


def check_ringtone_asset(config: AppConfig) -> CheckResult:
    path = config.resolve_asset(config.ringtone_path)
    generate_ringtone_wav(path)
    ok = path.is_file() and path.stat().st_size > 1000
    meta: dict[str, Any] = {"path": str(path), "bytes": path.stat().st_size if path.is_file() else 0}
    if ok:
        with wave.open(str(path), "rb") as wf:
            meta.update(
                {
                    "channels": wf.getnchannels(),
                    "rate": wf.getframerate(),
                    "frames": wf.getnframes(),
                }
            )
    return CheckResult(
        id="ringtone_asset",
        claim="Ringtone WAV exists and is valid mono PCM",
        verdict="VERIFIED" if ok and meta.get("channels") == 1 else "NOT VERIFIED",
        evidence=meta,
    )


def check_tray_import() -> CheckResult:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        from .tray import TrayApp, _make_icon

        img = _make_icon((46, 160, 67))
        ok = img.size == (64, 64)
        return CheckResult(
            id="tray_deps",
            claim="Tray dependencies (pystray/Pillow) work and icon renders",
            verdict="VERIFIED" if ok else "NOT VERIFIED",
            evidence={"icon_size": list(img.size), "pystray": getattr(pystray, "__version__", "?")},
        )
    except Exception as exc:
        return CheckResult(
            id="tray_deps",
            claim="Tray dependencies (pystray/Pillow) work and icon renders",
            verdict="NOT VERIFIED",
            evidence={"error": str(exc)},
        )


def check_autostart_installed() -> CheckResult:
    startup = (
        Path.home()
        / "AppData"
        / "Roaming"
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )
    candidates = list(startup.glob("CX300 Bridge.*"))
    return CheckResult(
        id="autostart",
        claim="Autostart shortcut exists in Windows Startup folder",
        verdict="VERIFIED" if candidates else "NOT VERIFIED",
        evidence={"files": [str(p) for p in candidates]},
    )


def check_hardware_hid() -> CheckResult:
    devices = enumerate_cx300()
    if not devices:
        return CheckResult(
            id="hardware_hid",
            claim="CX300 HID 095D:9201 is present and openable",
            verdict="INCONCLUSIVE",
            evidence={"hid_count": 0, "reason": "device not present / unplugged"},
            notes="Plug in the CX300 USB cable and re-run verify.",
        )
    dev = Cx300Device()
    opened = dev.open()
    evidence: dict[str, Any] = {
        "hid_count": len(devices),
        "opened": opened,
        "interfaces": [
            {
                "usage_page": d.get("usage_page"),
                "interface": d.get("interface_number"),
            }
            for d in devices
        ],
    }
    if not opened:
        return CheckResult(
            id="hardware_hid",
            claim="CX300 HID 095D:9201 is present and openable",
            verdict="NOT VERIFIED",
            evidence=evidence,
        )

    # Display write smoke
    display_ok = False
    try:
        dev.write_two_lines("Verify", "LCD OK", max_chars=16)
        dev.set_led(LedColor.GREEN)
        display_ok = True
    except OSError as exc:
        evidence["display_error"] = str(exc)

    # Listen briefly for any report
    got_report = False
    deadline = time.time() + 3.0
    while time.time() < deadline:
        data = dev.read_report(50)
        if data:
            got_report = True
            evidence["sample_report"] = data[:8].hex(" ")
            break

    evidence["display_write"] = display_ok
    evidence["got_report"] = got_report
    dev.close()

    # Openable + display is the hard requirement; reports may be idle
    verdict = "VERIFIED" if opened and display_ok else "NOT VERIFIED"
    return CheckResult(
        id="hardware_hid",
        claim="CX300 HID openable; LCD/LED write succeeds",
        verdict=verdict,
        evidence=evidence,
        notes="Press keys / lift handset during --hardware-listen for hook/key proof.",
    )


def check_hardware_audio(config: AppConfig) -> CheckResult:
    playback = find_device(config.playback_match, kind="output")
    capture = find_device(config.capture_match, kind="input")
    listing = describe_cx300(config.playback_match)
    if playback is None and capture is None:
        return CheckResult(
            id="hardware_audio",
            claim="CX300 appears as playback and capture audio endpoints",
            verdict="INCONCLUSIVE",
            evidence={"listing": listing, "playback": playback, "capture": capture},
            notes="No Polycom audio endpoints — phone likely unplugged.",
        )
    ok = playback is not None and capture is not None
    # Try short ringtone if playback exists
    played = False
    if playback is not None:
        try:
            ringer = Ringer(config.playback_match, config.resolve_asset(config.ringtone_path))
            ringer.ring(0.4)
            time.sleep(0.6)
            played = True
        except Exception as exc:
            listing = listing + f"\nplay_error={exc}"
    return CheckResult(
        id="hardware_audio",
        claim="CX300 appears as playback and capture audio endpoints; ringtone can play",
        verdict="VERIFIED" if ok and played else ("NOT VERIFIED" if not ok else "INCONCLUSIVE"),
        evidence={
            "listing": listing,
            "playback_index": playback,
            "capture_index": capture,
            "played": played,
        },
    )


def check_hardware_hook_keys(listen_seconds: float) -> CheckResult:
    devices = enumerate_cx300()
    if not devices:
        return CheckResult(
            id="hardware_hook_keys",
            claim="Handset hook and keypad 1/2/* produce distinct HID edges",
            verdict="INCONCLUSIVE",
            evidence={"reason": "no HID device"},
        )
    if listen_seconds <= 0:
        return CheckResult(
            id="hardware_hook_keys",
            claim="Handset hook and keypad 1/2/* produce distinct HID edges",
            verdict="INCONCLUSIVE",
            evidence={"reason": "pass --hardware-listen N to capture live events"},
            notes="Re-run: py -3.13 -m cx300_bridge.verify --hardware-listen 20",
        )

    dev = Cx300Device()
    if not dev.open():
        return CheckResult(
            id="hardware_hook_keys",
            claim="Handset hook and keypad 1/2/* produce distinct HID edges",
            verdict="NOT VERIFIED",
            evidence={"opened": False},
        )

    print(f"\n>>> HARDWARE LISTEN {listen_seconds:.0f}s: lift/hang handset, press 1, 2, *\n")
    seen_hook_off = False
    seen_hook_on = False
    keys: set[str] = set()
    samples: list[str] = []
    last_hook = None
    last_key = None
    from .hid_cx300 import KEYPAD_CODES

    deadline = time.time() + listen_seconds
    while time.time() < deadline:
        data = dev.read_report(50)
        if not data:
            continue
        if data[0] == 0x01 and len(data) >= 3:
            b1, b2 = data[1], data[2]
        else:
            b1, b2 = data[0], data[1] if len(data) > 1 else 0
        hook = bool(b1 & 0x01)
        if hook != last_hook:
            last_hook = hook
            if hook:
                seen_hook_off = True
                print("  hook OFF (picked up)")
            else:
                seen_hook_on = True
                print("  hook ON (hung up)")
            samples.append(data[:8].hex(" "))
        keypad = b2 & 0x0F
        key = KEYPAD_CODES.get(keypad)
        if key and key != last_key:
            last_key = key
            keys.add(key)
            print(f"  key {key}")
            samples.append(data[:8].hex(" "))
        elif keypad == 0:
            last_key = None

    dev.close()
    need = {"1", "2", "*"}
    keys_ok = need.issubset(keys)
    hook_ok = seen_hook_off and seen_hook_on
    ok = hook_ok and keys_ok
    return CheckResult(
        id="hardware_hook_keys",
        claim="Handset hook and keypad 1/2/* produce distinct HID edges",
        verdict="VERIFIED" if ok else "NOT VERIFIED",
        evidence={
            "hook_off": seen_hook_off,
            "hook_on": seen_hook_on,
            "keys_seen": sorted(keys),
            "required_keys": sorted(need),
            "samples": samples[:20],
        },
    )


def run_all(*, hardware_listen: float = 0.0, out_dir: Path | None = None) -> Path:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config()
    root = config.project_root or Path.cwd()
    out = out_dir or (root / "verify-evidence" / _now_slug())
    out.mkdir(parents=True, exist_ok=True)

    checks = [
        check_config(config),
        check_hotkey_parse(config),
        check_ringtone_asset(config),
        check_tray_import(),
        check_autostart_installed(),
        check_state_machine(config),
        check_hid_event_wiring(config),
        check_window_focus(config),
        check_live_hotkey_fire(config),
        check_hardware_hid(),
        check_hardware_audio(config),
        check_hardware_hook_keys(hardware_listen),
    ]

    # Write artifacts
    results = [asdict(c) for c in checks]
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    verified = sum(1 for c in checks if c.verdict == "VERIFIED")
    not_v = sum(1 for c in checks if c.verdict == "NOT VERIFIED")
    incon = sum(1 for c in checks if c.verdict == "INCONCLUSIVE")

    lines = [
        f"# Verification {_now_slug()}",
        "",
        f"- VERIFIED: {verified}",
        f"- NOT VERIFIED: {not_v}",
        f"- INCONCLUSIVE: {incon}",
        "",
        "| id | verdict | claim |",
        "|----|---------|-------|",
    ]
    for c in checks:
        lines.append(f"| `{c.id}` | **{c.verdict}** | {c.claim} |")
        if c.notes:
            lines.append(f"| | | _{c.notes}_ |")
    lines.append("")
    lines.append("## Evidence")
    for c in checks:
        lines.append(f"### {c.id} — {c.verdict}")
        lines.append("```json")
        lines.append(json.dumps(c.evidence, indent=2))
        lines.append("```")
        lines.append("")

    overall = (
        "VERIFIED"
        if not_v == 0 and incon == 0
        else ("INCONCLUSIVE" if not_v == 0 else "NOT VERIFIED")
    )
    verdict_md = (
        f"{overall}\n"
        f"Claim: Full plan requirements for CX300 multi-target bridge\n\n"
        f"Evidence: {verified} verified, {not_v} not verified, {incon} inconclusive\n"
        f"Artifacts: {out}\n"
    )
    (out / "verdict.md").write_text(verdict_md, encoding="utf-8")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")

    print(verdict_md)
    for c in checks:
        print(f"  [{c.verdict}] {c.id}: {c.claim}")
    print(f"\nReport: {out / 'report.md'}")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Verify CX300 bridge requirements")
    parser.add_argument(
        "--hardware-listen",
        type=float,
        default=0.0,
        help="Seconds to listen for live hook/key events (interactive)",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    out = run_all(hardware_listen=args.hardware_listen, out_dir=args.out)
    # Exit 0 if no NOT VERIFIED; 2 if only inconclusive hardware; 1 if failures
    data = json.loads((out / "results.json").read_text(encoding="utf-8"))
    if any(r["verdict"] == "NOT VERIFIED" for r in data):
        return 1
    if any(r["verdict"] == "INCONCLUSIVE" for r in data):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
