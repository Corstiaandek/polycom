from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .audio_devices import describe_cx300
from .config import AppConfig, TargetSpec, load_config
from .display import DisplayController
from .done_watch import DoneWatch
from .hid_cx300 import Cx300Event, Cx300Listener, LedColor
from .ringer import Ringer
from .state import BridgeState
from .tray import TrayApp
from .window_ui import WindowController

log = logging.getLogger(__name__)


class BridgeApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = BridgeState.IDLE
        self.active_index = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._clock_thread: threading.Thread | None = None

        self.windows = WindowController()
        self.listener = Cx300Listener(
            on_event=self._on_hid_event,
            reconnect_seconds=config.reconnect_seconds,
        )
        self.display = DisplayController(
            self.listener.device,
            line_max=config.line1_max,
        )
        self.ringer = Ringer(
            playback_match=config.playback_match,
            ringtone_path=config.resolve_asset(config.ringtone_path),
        )
        self.done_watch = DoneWatch(on_done=self._on_done)
        self.tray: TrayApp | None = None

    @property
    def active_target(self) -> TargetSpec:
        return self.config.targets[self.active_index]

    def start(self) -> None:
        logging.basicConfig(
            level=getattr(logging, self.config.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
        log.info("Starting CX300 bridge (config=%s)", self.config.config_path)
        self.ringer.ensure_ringtone()

        self.tray = TrayApp(
            get_status=self.status_text,
            on_select_target=self.select_target_id,
            target_labels=[(t.id, t.label) for t in self.config.targets],
            on_pause=self.pause,
            on_resume=self.resume,
            on_test_ring=self.test_ring,
            on_quit=self.stop,
        )
        self.tray.start()
        self.listener.start()
        if self.config.idle_clock:
            self._clock_thread = threading.Thread(
                target=self._clock_loop, name="cx300-clock", daemon=True
            )
            self._clock_thread.start()
        self._refresh_ui()
        log.info("Bridge running. Active target: %s", self.active_target.label)

    def stop(self) -> None:
        log.info("Stopping bridge")
        self._stop.set()
        self.done_watch.cancel()
        self.listener.stop()
        if self.tray:
            self.tray.stop()

    def wait(self) -> None:
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop()

    def status_text(self) -> str:
        return f"{self.active_target.label} | {self.state.name.title()}"

    def select_target_id(self, target_id: str) -> None:
        with self._lock:
            if self.state not in (BridgeState.IDLE, BridgeState.ERROR, BridgeState.PAUSED):
                log.info("Ignore target switch while %s", self.state.name)
                self.display.error(self.active_target.label, "Bezig opname")
                return
            for i, t in enumerate(self.config.targets):
                if t.id == target_id:
                    self.active_index = i
                    break
            else:
                return
            target = self.active_target
            self.display.target_switched(target.label)
            self.windows.ensure_focused(target, timeout=5.0)
            self.state = BridgeState.IDLE
            self._refresh_ui()
            # Brief "Gereed" then idle text
            threading.Timer(1.2, self._refresh_ui).start()

    def cycle_target(self) -> None:
        with self._lock:
            if self.state not in (BridgeState.IDLE, BridgeState.ERROR, BridgeState.PAUSED):
                return
            self.active_index = (self.active_index + 1) % len(self.config.targets)
        self.select_target_id(self.active_target.id)

    def pause(self) -> None:
        with self._lock:
            self.done_watch.cancel()
            self.state = BridgeState.PAUSED
            self.display.paused()
            self._refresh_ui()

    def resume(self) -> None:
        with self._lock:
            self.state = BridgeState.IDLE
            self._refresh_ui()

    def test_ring(self) -> None:
        with self._lock:
            self.state = BridgeState.RINGING
            self.display.ringing(self.active_target.label)
            self._refresh_ui()
        self.ringer.ring(self.config.ringtone_duration_seconds)

        def back_idle() -> None:
            with self._lock:
                if self.state == BridgeState.RINGING:
                    self.state = BridgeState.IDLE
                    self._refresh_ui()

        threading.Timer(self.config.ringtone_duration_seconds + 0.3, back_idle).start()

    def _on_hid_event(self, event: Cx300Event) -> None:
        log.debug("HID event %s key=%s", event.kind, event.key)
        if event.kind == "connected":
            self.display.invalidate()
            with self._lock:
                if self.state != BridgeState.PAUSED:
                    self.state = BridgeState.IDLE
            self._refresh_ui()
            return
        if event.kind == "disconnected":
            log.warning("CX300 disconnected")
            return

        with self._lock:
            if self.state == BridgeState.PAUSED:
                return

            if event.kind == "key" and event.key:
                self._handle_key(event.key)
                return

            if event.kind == "hook_off":
                self._start_recording()
                return

            if event.kind == "hook_on":
                self._submit_recording()
                return

    def _handle_key(self, key: str) -> None:
        if self.state not in (BridgeState.IDLE, BridgeState.ERROR, BridgeState.RINGING):
            log.info("Key %s ignored in state %s", key, self.state.name)
            return
        action = self.config.keys.get(key)
        if not action:
            return
        if action == "cycle":
            # release lock via cycle_target which re-acquires
            pass
        # Call outside careful re-entry: we're already holding lock
        if action == "cycle":
            self.active_index = (self.active_index + 1) % len(self.config.targets)
            target = self.active_target
            self.display.target_switched(target.label)
            threading.Thread(
                target=self.windows.ensure_focused,
                args=(target,),
                kwargs={"timeout": 5.0},
                daemon=True,
            ).start()
            self.state = BridgeState.IDLE
            self._refresh_ui()
            return

        for i, t in enumerate(self.config.targets):
            if t.id == action:
                self.active_index = i
                target = self.active_target
                self.display.target_switched(target.label)
                threading.Thread(
                    target=self.windows.ensure_focused,
                    args=(target,),
                    kwargs={"timeout": 5.0},
                    daemon=True,
                ).start()
                self.state = BridgeState.IDLE
                self._refresh_ui()
                return

    def _start_recording(self) -> None:
        if self.state == BridgeState.RECORDING:
            return
        self.done_watch.cancel()
        target = self.active_target
        self.state = BridgeState.RECORDING
        self.display.recording(target.label)
        self._refresh_ui()

        def work() -> None:
            ok = self.windows.start_voice(target)
            if not ok:
                with self._lock:
                    self.state = BridgeState.ERROR
                    self.display.error(target.label, "App niet open?")
                    self._refresh_ui()

        threading.Thread(target=work, name="start-voice", daemon=True).start()

    def _submit_recording(self) -> None:
        if self.state != BridgeState.RECORDING:
            # Hangup while idle / waiting: ignore
            return
        target = self.active_target
        self.state = BridgeState.WAITING
        self.display.waiting(target.label)
        self._refresh_ui()

        def work() -> None:
            ok = self.windows.send_prompt(target)
            with self._lock:
                if not ok:
                    self.state = BridgeState.ERROR
                    self.display.error(target.label, "Send mislukt")
                    self._refresh_ui()
                    return
                self.done_watch.arm(target.done_idle_seconds)

        threading.Thread(target=work, name="send-prompt", daemon=True).start()

    def _on_done(self) -> None:
        with self._lock:
            if self.state not in (BridgeState.WAITING, BridgeState.IDLE):
                return
            self.state = BridgeState.RINGING
            self.display.ringing(self.active_target.label)
            self._refresh_ui()
        self.ringer.ring(self.config.ringtone_duration_seconds)

        def back_idle() -> None:
            with self._lock:
                if self.state == BridgeState.RINGING:
                    self.state = BridgeState.IDLE
                    self._refresh_ui()

        threading.Timer(self.config.ringtone_duration_seconds + 0.5, back_idle).start()

    def _refresh_ui(self) -> None:
        target = self.active_target
        state = self.state
        if state == BridgeState.IDLE:
            self.display.idle(target.label, with_clock=False)
        elif state == BridgeState.RECORDING:
            self.display.recording(target.label)
        elif state == BridgeState.WAITING:
            self.display.waiting(target.label)
        elif state == BridgeState.RINGING:
            self.display.ringing(target.label)
        elif state == BridgeState.PAUSED:
            self.display.paused()
        elif state == BridgeState.ERROR:
            self.display.error(target.label, "Fout")
        if self.tray:
            self.tray.set_visual(state.name.lower(), self.status_text())

    def _clock_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if (
                    self.state == BridgeState.IDLE
                    and self.config.idle_clock
                    and self.listener.device.connected
                ):
                    # Alternate clock every other tick minute display
                    line2 = datetime.now().strftime("%H:%M")
                    # Keep target on line1; only refresh clock when minute changes
                    self.display.show(self.active_target.label, line2, LedColor.GREEN)
            self._stop.wait(30.0)


def install_autostart(config: AppConfig) -> Path:
    """Create a Startup folder .lnk that runs the bridge."""
    import sys

    startup = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True, exist_ok=True)
    lnk_path = startup / "CX300 Bridge.lnk"

    # Prefer pythonw for no console
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = str(pythonw if pythonw.is_file() else python)
    # Module invocation
    args = f'-m cx300_bridge.app --config "{config.config_path}"'
    workdir = str(config.project_root or Path.cwd())

    try:
        from win32com.client import Dispatch

        shell = Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(lnk_path))
        shortcut.Targetpath = exe
        shortcut.Arguments = args
        shortcut.WorkingDirectory = workdir
        shortcut.IconLocation = exe
        shortcut.save()
    except Exception:
        # Fallback: write a .cmd
        cmd_path = startup / "CX300 Bridge.cmd"
        cmd_path.write_text(
            f'@echo off\r\ncd /d "{workdir}"\r\n"{exe}" {args}\r\n',
            encoding="utf-8",
        )
        return cmd_path
    return lnk_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polycom CX300 multi-target bridge")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.toml")
    parser.add_argument("--install-autostart", action="store_true")
    parser.add_argument("--list-audio", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)

    if args.list_audio:
        print(describe_cx300(config.playback_match))
        return 0

    if args.install_autostart:
        path = install_autostart(config)
        print(f"Autostart installed: {path}")
        return 0

    app = BridgeApp(config)
    app.start()
    app.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
