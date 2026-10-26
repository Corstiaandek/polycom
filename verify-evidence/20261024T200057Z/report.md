# Verification 20261024T200059Z

- VERIFIED: 7
- NOT VERIFIED: 0
- INCONCLUSIVE: 3

| id | verdict | claim |
|----|---------|-------|
| `config_targets_keys` | **VERIFIED** | config.toml defines grok+claude targets and keys 1/2/* mapping |
| `hotkey_parse` | **VERIFIED** | Per-target voice/send hotkeys parse to pynput key sequences |
| `ringtone_asset` | **VERIFIED** | Ringtone WAV exists and is valid mono PCM |
| `tray_deps` | **VERIFIED** | Tray dependencies (pystray/Pillow) work and icon renders |
| `autostart` | **VERIFIED** | Autostart shortcut exists in Windows Startup folder |
| `state_machine` | **VERIFIED** | Keys 1/2/* switch targets in Idle; hook_off→RECORDING+voice; hook_on→WAITING+send; done→RINGING; keys blocked while recording |
| `window_focus` | **VERIFIED** | Both Grok Bot and Claude windows can be found and focused |
| `hardware_hid` | **INCONCLUSIVE** | CX300 HID 095D:9201 is present and openable |
| | | _Plug in the CX300 USB cable and re-run verify._ |
| `hardware_audio` | **INCONCLUSIVE** | CX300 appears as playback and capture audio endpoints |
| | | _No Polycom audio endpoints — phone likely unplugged._ |
| `hardware_hook_keys` | **INCONCLUSIVE** | Handset hook and keypad 1/2/* produce distinct HID edges |

## Evidence
### config_targets_keys — VERIFIED
```json
{
  "target_ids": [
    "grok",
    "claude"
  ],
  "keys": {
    "1": "grok",
    "2": "claude",
    "*": "cycle"
  }
}
```

### hotkey_parse — VERIFIED
```json
{
  "grok": {
    "voice": [
      "<Key.ctrl: <17>>",
      "'d'"
    ],
    "send": [
      "<Key.enter: <13>>"
    ]
  },
  "claude": {
    "voice": [
      "<Key.ctrl: <17>>",
      "'d'"
    ],
    "send": [
      "<Key.enter: <13>>"
    ]
  }
}
```

### ringtone_asset — VERIFIED
```json
{
  "path": "C:\\dev\\polycom\\assets\\ringtone.wav",
  "bytes": 80044,
  "channels": 1,
  "rate": 16000,
  "frames": 40000
}
```

### tray_deps — VERIFIED
```json
{
  "icon_size": [
    64,
    64
  ],
  "pystray": "?"
}
```

### autostart — VERIFIED
```json
{
  "files": [
    "C:\\Users\\ckruijf.cobot\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\CX300 Bridge.lnk"
  ]
}
```

### state_machine — VERIFIED
```json
{
  "timeline": [
    "after_key2=claude",
    "after_key1=grok",
    "after_star=claude",
    "after_hook_off=RECORDING",
    "key_during_rec_target=claude",
    "after_hook_on=WAITING",
    "after_done=RINGING"
  ],
  "calls": [
    "start_voice:claude",
    "send_prompt:claude",
    "ring"
  ]
}
```

### window_focus — VERIFIED
```json
{
  "grok": {
    "launched": false,
    "windows": [
      "Grok Bot"
    ],
    "focused": true
  },
  "claude": {
    "launched": false,
    "windows": [
      "Claude"
    ],
    "focused": true
  }
}
```

### hardware_hid — INCONCLUSIVE
```json
{
  "hid_count": 0,
  "reason": "device not present / unplugged"
}
```

### hardware_audio — INCONCLUSIVE
```json
{
  "listing": "(no devices matching 'Polycom CX300')",
  "playback": null,
  "capture": null
}
```

### hardware_hook_keys — INCONCLUSIVE
```json
{
  "reason": "no HID device"
}
```
