from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _expand(path: str) -> str:
    if not path:
        return ""
    return os.path.expandvars(os.path.expanduser(path))


@dataclass
class TargetSpec:
    id: str
    label: str
    window_title_regex: str
    exe: str = ""
    shell_app_id: str = ""
    voice_hotkey: str = "<ctrl>+d"
    send_hotkey: str = "<enter>"
    done_idle_seconds: float = 8.0


@dataclass
class AppConfig:
    device_name_substring: str = "Polycom CX300"
    reconnect_seconds: float = 2.0
    idle_clock: bool = True
    keys: dict[str, str] = field(default_factory=dict)
    targets: list[TargetSpec] = field(default_factory=list)
    line1_max: int = 16
    line2_max: int = 16
    playback_match: str = "Polycom CX300"
    capture_match: str = "Polycom CX300"
    ringtone_path: str = "assets/ringtone.wav"
    ringtone_duration_seconds: float = 2.5
    log_level: str = "INFO"
    autostart_enabled: bool = False
    config_path: Path | None = None
    project_root: Path | None = None

    def target_by_id(self, target_id: str) -> TargetSpec | None:
        for t in self.targets:
            if t.id == target_id:
                return t
        return None

    def resolve_asset(self, relative: str) -> Path:
        root = self.project_root or Path.cwd()
        p = Path(_expand(relative))
        if p.is_absolute():
            return p
        return (root / p).resolve()


def default_config_paths() -> list[Path]:
    here = Path(__file__).resolve()
    project = here.parents[2]
    return [
        Path.cwd() / "config.toml",
        project / "config.toml",
    ]


def load_config(path: Path | None = None) -> AppConfig:
    cfg_path = path
    if cfg_path is None:
        for candidate in default_config_paths():
            if candidate.is_file():
                cfg_path = candidate
                break
    if cfg_path is None or not cfg_path.is_file():
        raise FileNotFoundError("config.toml not found (looked in cwd and project root)")

    raw: dict[str, Any]
    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)

    targets: list[TargetSpec] = []
    for item in raw.get("targets", []):
        targets.append(
            TargetSpec(
                id=str(item["id"]),
                label=str(item.get("label", item["id"])),
                window_title_regex=str(item.get("window_title_regex", "")),
                exe=_expand(str(item.get("exe", ""))),
                shell_app_id=str(item.get("shell_app_id", "")),
                voice_hotkey=str(item.get("voice_hotkey", "<ctrl>+d")),
                send_hotkey=str(item.get("send_hotkey", "<enter>")),
                done_idle_seconds=float(item.get("done_idle_seconds", 8.0)),
            )
        )
    if not targets:
        raise ValueError("config.toml must define at least one [[targets]] entry")

    keys = {str(k): str(v) for k, v in raw.get("keys", {}).items()}
    display = raw.get("display", {})
    audio = raw.get("audio", {})
    logging_cfg = raw.get("logging", {})
    autostart = raw.get("autostart", {})

    project_root = cfg_path.resolve().parent
    return AppConfig(
        device_name_substring=str(raw.get("device_name_substring", "Polycom CX300")),
        reconnect_seconds=float(raw.get("reconnect_seconds", 2.0)),
        idle_clock=bool(raw.get("idle_clock", True)),
        keys=keys or {"1": "grok", "2": "claude", "*": "cycle"},
        targets=targets,
        line1_max=int(display.get("line1_max", 16)),
        line2_max=int(display.get("line2_max", 16)),
        playback_match=str(audio.get("playback_match", "Polycom CX300")),
        capture_match=str(audio.get("capture_match", "Polycom CX300")),
        ringtone_path=str(audio.get("ringtone_path", "assets/ringtone.wav")),
        ringtone_duration_seconds=float(audio.get("ringtone_duration_seconds", 2.5)),
        log_level=str(logging_cfg.get("level", "INFO")),
        autostart_enabled=bool(autostart.get("enabled", False)),
        config_path=cfg_path,
        project_root=project_root,
    )
