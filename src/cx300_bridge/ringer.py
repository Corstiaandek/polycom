from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from .audio_devices import find_device

log = logging.getLogger(__name__)


def generate_ringtone_wav(path: Path, duration: float = 2.5, sample_rate: int = 16000) -> None:
    """Write a simple dual-tone ringtone WAV (CX300 likes 16 kHz mono)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    # Classic phone-ish cadence: 400+450 Hz bursts
    tone = 0.35 * (
        np.sin(2 * np.pi * 440 * t) + 0.5 * np.sin(2 * np.pi * 480 * t)
    )
    envelope = ((t % 1.0) < 0.4).astype(np.float64)
    audio = (tone * envelope * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())


class Ringer:
    def __init__(self, playback_match: str, ringtone_path: Path) -> None:
        self._playback_match = playback_match
        self._ringtone_path = ringtone_path
        self._lock = threading.Lock()
        self._playing = False

    def ensure_ringtone(self) -> None:
        if not self._ringtone_path.is_file():
            log.info("Generating ringtone at %s", self._ringtone_path)
            generate_ringtone_wav(self._ringtone_path)

    def ring(self, duration: float | None = None) -> None:
        self.ensure_ringtone()
        thread = threading.Thread(
            target=self._play,
            args=(duration,),
            name="cx300-ringer",
            daemon=True,
        )
        thread.start()

    def _play(self, duration: float | None) -> None:
        with self._lock:
            if self._playing:
                return
            self._playing = True
        try:
            device = find_device(self._playback_match, kind="output")
            with wave.open(str(self._ringtone_path), "rb") as wf:
                rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
            if sampwidth == 2:
                data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                log.warning("Unsupported sample width %s", sampwidth)
                return
            if channels > 1:
                data = data.reshape(-1, channels).mean(axis=1)
            if duration is not None:
                data = data[: int(rate * duration)]
            sd.play(data, samplerate=rate, device=device, blocking=True)
            sd.stop()
        except Exception as exc:
            log.warning("Ringtone playback failed: %s", exc)
        finally:
            with self._lock:
                self._playing = False
