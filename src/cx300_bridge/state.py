from __future__ import annotations

from enum import Enum, auto


class BridgeState(Enum):
    IDLE = auto()
    RECORDING = auto()
    WAITING = auto()
    RINGING = auto()
    PAUSED = auto()
    ERROR = auto()
