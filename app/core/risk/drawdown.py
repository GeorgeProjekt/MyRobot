from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional


@dataclass
class DrawdownSnapshot:
    pair: str
    peak_equity: float
    last_equity: float
    drawdown_pct: float
    max_drawdown_seen: float
    halted: bool
    halt_reason: Optional[str]
    cooldown_until_ts: Optional[float]
    day_key: Optional[str]
    day_start_equity: float
    daily_loss_pct: float
    consecutive_failures: int
    updated_ts: float


@dataclass
class _State:
    peak_equity: float = 0.0
    last_equity: float = 0.0
    drawdown_pct: float = 0.0
    max_drawdown_seen: float = 0.0

    halted: bool = False
    halt_reason: Optional[str] = None
    cooldown_until_ts: Optional[float] = None

    day_key: Optional[str] = None
    day_start_equity: float = 0.0
    daily_loss_pct: float = 0.0

    consecutive_failures: int = 0
    updated_ts: float = 0.0


class DrawdownGuard:
    """
    Persistent pair-isolated drawdown and daily-loss guard.

    Public API:
    - update(equity)
    - can_trade()
    - register_failure()
    - register_success()
    - snapshot()
    - reset()
    """

    def __init__(
        self,
        *,
        pair: str,
        max_drawdown_pct: float = 0.15,
        max_daily_loss_pct: float = 0.05,
        cooldown_seconds: float = 300.0,
        min_equity_to_track: float = 1.0,
        max_consecutive_failures: int = 5,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair).upper().strip()

        self.max_drawdown_pct = max(float(max_drawdown_pct), 0.0)
        self.max_daily_loss_pct = max(float(max_daily_loss_pct), 0.0)
        self.cooldown_seconds = max(float(cooldown_seconds), 0.0)
        self.min_equity_to_track = max(float(min_equity_to_track), 0.0)
        self.max_consecutive_failures = max(int(max_consecutive_failures), 0)

        self._lock = RLock()
        self._state = _State()

        base_dir = Path(state_dir or (Path("runtime") / "drawdown")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_drawdown.json"

        self._load()

    # ---------------------------------------------------------
    # PUBLIC API
    # ---------------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self._state = _State(updated_ts=time.time())
            self._save()

    def update(self, equity: float, now_ts: Optional[float] = None) -> DrawdownSnapshot:
        with self._lock:
            now = float(now_ts if now_ts is not None else time.time())
            eq = float(equity or 0.0)

            self._state.updated_ts = now
            self._state.last_equity = eq

            self._update_day_state(eq, now)
            self._refresh_halt(now)

            if eq < self.min_equity_to_track:
                self._state.drawdown_pct = 0.0
                self._save()
                return self.snapshot()

            if self._state.peak_equity <= 0.0:
                self._state.peak_equity = eq
                self._state.drawdown_pct = 0.0
                self._save()
                return self.snapshot()

            if eq > self._state.peak_equity:
                self._state.peak_equity = eq

            peak = self._state.peak_equity
            dd = 0.0 if peak <= 0.0 else max(0.0, (peak - eq) / peak)
            self._state.drawdown_pct = float(dd)

            if dd > self._state.max_drawdown_seen:
                self._state.max_drawdown_seen = float(dd)

            self._apply_hard_limits(now)
            self._save()
            return self.snapshot()

    def can_trade(self, now_ts: Optional[float] = None) -> bool:
        with self._lock:
            now = float(now_ts if now_ts is not None else time.time())
            self._refresh_halt(now)
            self._save()
            return not self._state.halted

    def register_failure(self, now_ts: Optional[float] = None) -> DrawdownSnapshot:
        with self._lock:
            now = float(now_ts if now_ts is not None else time.time())
            self._state.updated_ts = now
            self._state.consecutive_failures += 1

            if self.max_consecutive_failures > 0 and self._state.consecutive_failures >= self.max_consecutive_failures:
                self._halt(
                    reason=(
                        "max_consecutive_failures_exceeded "
                        f"({self._state.consecutive_failures} >= {self.max_consecutive_failures})"
                    ),
                    now_ts=now,
                )

            self._save()
            return self.snapshot()

    def register_success(self) -> DrawdownSnapshot:
        with self._lock:
            self._state.consecutive_failures = 0
            self._state.updated_ts = time.time()
            self._save()
            return self.snapshot()

    def snapshot(self) -> DrawdownSnapshot:
        with self._lock:
            state = self._state
            return DrawdownSnapshot(
                pair=self.pair,
                peak_equity=float(state.peak_equity),
                last_equity=float(state.last_equity),
                drawdown_pct=float(state.drawdown_pct),
                max_drawdown_seen=float(state.max_drawdown_seen),
                halted=bool(state.halted),
                halt_reason=state.halt_reason,
                cooldown_until_ts=state.cooldown_until_ts,
                day_key=state.day_key,
                day_start_equity=float(state.day_start_equity),
                daily_loss_pct=float(state.daily_loss_pct),
                consecutive_failures=int(state.consecutive_failures),
                updated_ts=float(state.updated_ts or 0.0),
            )

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _update_day_state(self, equity: float, now_ts: float) -> None:
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now_ts))

        if self._state.day_key != day_key:
            self._state.day_key = day_key
            self._state.day_start_equity = max(float(equity or 0.0), 0.0)
            self._state.daily_loss_pct = 0.0

        start = float(self._state.day_start_equity or 0.0)
        if start > 0.0 and equity > 0.0:
            self._state.daily_loss_pct = max(0.0, (start - equity) / start)
        else:
            self._state.daily_loss_pct = 0.0

    def _apply_hard_limits(self, now_ts: float) -> None:
        if self._state.halted:
            return

        if self.max_drawdown_pct > 0.0 and self._state.drawdown_pct >= self.max_drawdown_pct:
            self._halt(
                reason=f"max_drawdown_exceeded ({self._state.drawdown_pct:.4f} >= {self.max_drawdown_pct:.4f})",
                now_ts=now_ts,
            )
            return

        if self.max_daily_loss_pct > 0.0 and self._state.daily_loss_pct >= self.max_daily_loss_pct:
            self._halt(
                reason=f"max_daily_loss_exceeded ({self._state.daily_loss_pct:.4f} >= {self.max_daily_loss_pct:.4f})",
                now_ts=now_ts,
            )

    def _halt(self, *, reason: str, now_ts: float) -> None:
        self._state.halted = True
        self._state.halt_reason = reason
        if self.cooldown_seconds > 0.0:
            self._state.cooldown_until_ts = now_ts + self.cooldown_seconds
        else:
            self._state.cooldown_until_ts = None

    def _refresh_halt(self, now_ts: float) -> None:
        until = self._state.cooldown_until_ts
        if until is None:
            return

        if now_ts >= float(until):
            self._state.halted = False
            self._state.halt_reason = None
            self._state.cooldown_until_ts = None
            self._state.consecutive_failures = 0

    # ---------------------------------------------------------
    # PERSISTENCE
    # ---------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            if not self._state_file.exists():
                return

            try:
                payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            except Exception:
                return

            state = payload.get("state")
            if not isinstance(state, dict):
                return

            try:
                self._state = _State(
                    peak_equity=float(state.get("peak_equity") or 0.0),
                    last_equity=float(state.get("last_equity") or 0.0),
                    drawdown_pct=float(state.get("drawdown_pct") or 0.0),
                    max_drawdown_seen=float(state.get("max_drawdown_seen") or 0.0),
                    halted=bool(state.get("halted", False)),
                    halt_reason=state.get("halt_reason"),
                    cooldown_until_ts=(
                        float(state["cooldown_until_ts"])
                        if state.get("cooldown_until_ts") is not None else None
                    ),
                    day_key=state.get("day_key"),
                    day_start_equity=float(state.get("day_start_equity") or 0.0),
                    daily_loss_pct=float(state.get("daily_loss_pct") or 0.0),
                    consecutive_failures=int(state.get("consecutive_failures") or 0),
                    updated_ts=float(state.get("updated_ts") or 0.0),
                )
            except Exception:
                self._state = _State()

    def _save(self) -> None:
        with self._lock:
            payload: Dict[str, Any] = {
                "pair": self.pair,
                "updated_ts": time.time(),
                "config": {
                    "max_drawdown_pct": self.max_drawdown_pct,
                    "max_daily_loss_pct": self.max_daily_loss_pct,
                    "cooldown_seconds": self.cooldown_seconds,
                    "min_equity_to_track": self.min_equity_to_track,
                    "max_consecutive_failures": self.max_consecutive_failures,
                },
                "state": asdict(self._state),
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )