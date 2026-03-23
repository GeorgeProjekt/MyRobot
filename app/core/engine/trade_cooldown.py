from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class TradeCooldownState:
    pair: str
    cooldown_seconds: float
    last_trade_ts: Optional[float] = None


class TradeCooldown:
    """
    Pair-isolated deterministic trade cooldown manager.

    Purpose:
    - prevent excessive trade frequency on one pair
    - expose stable diagnostics for runtime / dashboard
    - never affect other pairs
    """

    def __init__(self, pair: str, cooldown_seconds: float = 30.0) -> None:
        self._state = TradeCooldownState(
            pair=str(pair).upper().strip(),
            cooldown_seconds=max(float(cooldown_seconds), 0.0),
            last_trade_ts=None,
        )

    # -----------------------------------------------------

    def can_trade(self, now_ts: Optional[float] = None) -> bool:
        if self._state.last_trade_ts is None:
            return True

        elapsed = self._elapsed(now_ts=now_ts)
        return elapsed >= self._state.cooldown_seconds

    def register_trade(self, now_ts: Optional[float] = None) -> None:
        self._state.last_trade_ts = float(now_ts if now_ts is not None else time.time())

    def remaining(self, now_ts: Optional[float] = None) -> float:
        if self._state.last_trade_ts is None:
            return 0.0

        elapsed = self._elapsed(now_ts=now_ts)
        remaining = self._state.cooldown_seconds - elapsed
        return max(0.0, float(remaining))

    def reset(self) -> None:
        self._state.last_trade_ts = None

    def diagnostics(self, now_ts: Optional[float] = None) -> Dict[str, Any]:
        return {
            "pair": self._state.pair,
            "cooldown_seconds": float(self._state.cooldown_seconds),
            "remaining": float(self.remaining(now_ts=now_ts)),
            "last_trade_ts": self._state.last_trade_ts,
            "can_trade": bool(self.can_trade(now_ts=now_ts)),
        }

    # -----------------------------------------------------
    # INTERNALS
    # -----------------------------------------------------

    def _elapsed(self, now_ts: Optional[float] = None) -> float:
        if self._state.last_trade_ts is None:
            return float("inf")

        now = float(now_ts if now_ts is not None else time.time())
        return max(0.0, now - float(self._state.last_trade_ts))