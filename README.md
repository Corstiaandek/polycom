# Polycom CX300 ↔ Grok Bot / Claude bridge

Local Windows **system-tray** app that turns a Polycom CX300 USB phone into a voice prompt remote for **Grok Bot** and **Claude Desktop**.

| Action | Result |
|--------|--------|
| Key **1** | Select Grok Bot (focus/launch) |
| Key **2** | Select Claude |
| Key **\*** | Cycle targets |
| Pick up handset | Start voice input (`Ctrl+D` by default) |
| Hang up | Send prompt (Enter) |
| Bot “done” (idle timer) | Ringtone on CX300 + LCD `Klaar` |

Status lives on the **CX300 LCD + LEDs** and a small tray icon. No separate chat UI.

## Requirements

- Windows 10/11
- Python 3.11+
- Polycom CX300 plugged in (USB)
- [Grok Bot](https://cursor.com/download/bot) and/or Claude Desktop installed

## Install

```powershell
cd C:\dev\polycom
py -3.13 -m pip install -e .
```

## Run

```powershell
# Hardware smoke (hook / keys / display / audio list)
py -3.13 -m cx300_bridge.smoke

# Bridge (tray icon)
py -3.13 -m cx300_bridge.app

# List Polycom audio endpoints
py -3.13 -m cx300_bridge.app --list-audio

# Optional: start at login
py -3.13 -m cx300_bridge.app --install-autostart
```

Edit [`config.toml`](config.toml) for hotkeys, window title regexes, exe / Store app IDs, and keypad mapping.

## Tray menu

- **Targets** — switch Grok / Claude
- **Pause / Resume** — ignore handset
- **Test ring** — play ringtone on CX300 speaker
- **Quit**

## Verify (evidence)

```powershell
# Software + hardware probe (writes verify-evidence/<timestamp>/)
py -3.13 -m cx300_bridge.verify

# After plugging the CX300 — interactive hook/key capture
py -3.13 -m cx300_bridge.verify --hardware-listen 30
```

Artifacts: `verify-evidence/*/report.md`, `results.json`, `verdict.md`.

## Notes

- Display writes on Windows can be flaky; the smoke test verifies them on your machine.
- “Done” detection is a configurable idle timer after send (`done_idle_seconds` per target).
- Only the **active** target is focused on hook events.
- Full end-to-end handset verification requires the CX300 USB-connected (PnP Status OK, HID visible).
