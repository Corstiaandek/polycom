from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass

from pynput.keyboard import Controller, Key

from .config import TargetSpec

log = logging.getLogger(__name__)

# Optional win32 imports — module still importable on non-Windows for tests.
try:
    import win32con
    import win32gui
    import win32process

    HAS_WIN32 = True
except ImportError:  # pragma: no cover
    HAS_WIN32 = False


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int


def _parse_hotkey(hotkey: str) -> list:
    """Parse pynput-style '<ctrl>+d' into a list of Key/char to press together."""
    parts = [p.strip().lower() for p in hotkey.replace("-", "+").split("+") if p.strip()]
    mods: list = []
    key = None
    mapping = {
        "ctrl": Key.ctrl,
        "control": Key.ctrl,
        "alt": Key.alt,
        "shift": Key.shift,
        "cmd": Key.cmd,
        "win": Key.cmd,
        "enter": Key.enter,
        "return": Key.enter,
        "space": Key.space,
        "tab": Key.tab,
        "esc": Key.esc,
        "escape": Key.esc,
    }
    for part in parts:
        token = part.strip("<>")
        if token in mapping:
            k = mapping[token]
            if k in (Key.ctrl, Key.alt, Key.shift, Key.cmd):
                mods.append(k)
            else:
                key = k
        elif len(token) == 1:
            key = token
        else:
            raise ValueError(f"Unsupported hotkey token: {part!r} in {hotkey!r}")
    if key is None:
        raise ValueError(f"No key in hotkey: {hotkey!r}")
    return mods + [key]


class WindowController:
    def __init__(self) -> None:
        self._kb = Controller()

    def find_windows(self, title_regex: str) -> list[WindowInfo]:
        if not HAS_WIN32:
            return []
        pattern = re.compile(title_regex)
        found: list[WindowInfo] = []

        def enum_handler(hwnd: int, _: None) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title or not pattern.search(title):
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            found.append(WindowInfo(hwnd=hwnd, title=title, pid=pid))

        win32gui.EnumWindows(enum_handler, None)
        return found

    def focus_window(self, hwnd: int) -> bool:
        if not HAS_WIN32:
            return False
        try:
            import ctypes

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            # Allow foreground switch from background process (Windows restriction)
            try:
                ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
            except Exception:
                pass
            foreground = win32gui.GetForegroundWindow()
            if foreground:
                remote_thread, _ = win32process.GetWindowThreadProcessId(foreground)
                target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
                if remote_thread and target_thread and remote_thread != target_thread:
                    ctypes.windll.user32.AttachThreadInput(remote_thread, target_thread, True)
                    try:
                        win32gui.BringWindowToTop(hwnd)
                        win32gui.SetForegroundWindow(hwnd)
                    finally:
                        ctypes.windll.user32.AttachThreadInput(
                            remote_thread, target_thread, False
                        )
                else:
                    win32gui.SetForegroundWindow(hwnd)
            else:
                win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.15)
            return win32gui.GetForegroundWindow() == hwnd or True
        except Exception as exc:
            log.warning("SetForegroundWindow failed: %s", exc)
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                win32gui.BringWindowToTop(hwnd)
                return True
            except Exception as exc2:
                log.warning("BringWindowToTop failed: %s", exc2)
                return False

    def launch_target(self, target: TargetSpec) -> bool:
        if target.shell_app_id:
            app_id = target.shell_app_id
            try:
                # Prefer explorer shell:AppsFolder for Store / packaged apps
                subprocess.Popen(
                    ["explorer.exe", f"shell:AppsFolder\\{app_id}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("Launched shell app %s", app_id)
                return True
            except OSError as exc:
                log.warning("shell:AppsFolder launch failed: %s", exc)

        exe = target.exe
        if exe and os.path.isfile(exe):
            try:
                subprocess.Popen(
                    [exe],
                    cwd=os.path.dirname(exe) or None,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("Launched %s", exe)
                return True
            except OSError as exc:
                log.warning("Failed to launch %s: %s", exe, exc)
                return False

        log.warning("No launch path for target %s", target.id)
        return False

    def ensure_focused(self, target: TargetSpec, timeout: float = 8.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            wins = self.find_windows(target.window_title_regex)
            if wins:
                # Prefer longest title match / first visible
                if self.focus_window(wins[0].hwnd):
                    time.sleep(0.25)
                    return True
            time.sleep(0.4)
        # Not found — try launch then wait again
        if not self.launch_target(target):
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            wins = self.find_windows(target.window_title_regex)
            if wins and self.focus_window(wins[0].hwnd):
                time.sleep(0.4)
                return True
            time.sleep(0.4)
        return False

    def press_hotkey(self, hotkey: str) -> None:
        keys = _parse_hotkey(hotkey)
        mods = [k for k in keys if isinstance(k, Key) and k in (Key.ctrl, Key.alt, Key.shift, Key.cmd)]
        main = keys[-1]
        try:
            for m in mods:
                self._kb.press(m)
            self._kb.press(main)
            self._kb.release(main)
        finally:
            for m in reversed(mods):
                self._kb.release(m)
        log.info("Pressed hotkey %s", hotkey)

    def start_voice(self, target: TargetSpec) -> bool:
        if not self.ensure_focused(target):
            log.error("Could not focus target %s", target.id)
            return False
        self.press_hotkey(target.voice_hotkey)
        return True

    def send_prompt(self, target: TargetSpec) -> bool:
        if not self.ensure_focused(target, timeout=3.0):
            log.error("Could not focus target %s for send", target.id)
            return False
        # Brief pause so dictation can flush
        time.sleep(0.3)
        self.press_hotkey(target.send_hotkey)
        return True
