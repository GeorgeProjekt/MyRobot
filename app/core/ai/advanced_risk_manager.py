from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional


@dataclass
class DrawdownState:
    peak_equity: float = 0.0
    last_equity: float = 0.0
    drawdown: float = 0.0
    max_drawdown_seen: float = 0.0

    halted: bool = False
    halt_reason: Optional[str] = None
    cooldown_until_ts: Optional[float] = None

    last_day_key: Optional[str] = None
    day_start_equity: float = 0.0
    daily_loss_pct: float = 0.0

    consecutive_failures: int = 0


class AdvancedRiskManager:
    """
    Pair-isolated deterministic drawdown risk manager.

    Responsibilities:
    - track equity peak / drawdown
    - track daily loss
    - enforce cooldown halt
    - persist state so restart does not clear safety gates
    """

    def __init__(
        self,
        *,
        pair: str,
        max_drawdown_pct: float = 0.15,
        cooldown_seconds: float = 300.0,
        min_equity_to_track: float = 1.0,
        max_daily_loss_pct: float = 0.05,
        max_consecutive_failures: int = 5,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair).upper().strip()

        self.max_drawdown_pct = max(float(max_drawdown_pct), 0.0)
        self.cooldown_seconds = max(float(cooldown_seconds), 0.0)
        self.min_equity_to_track = max(float(min_equity_to_track), 0.0)
        self.max_daily_loss_pct = max(float(max_daily_loss_pct), 0.0)
        self.max_consecutive_failures = max(int(max_consecutive_failures), 0)

        self._lock = RLock()
        self.state = DrawdownState()

        base_dir = Path(state_dir or (Path("runtime") / "advanced_risk")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_drawdown_state.json"

        self._load_state()

    # -----------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self.state = DrawdownState()
            self._save_state()

    # -----------------------------------------------------

    def update_equity(self, equity: float, now_ts: Optional[float] = None) -> DrawdownState:
        with self._lock:
            now = float(now_ts if now_ts is not None else time.time())

            eq = float(equity or 0.0)
            self.state.last_equity = eq

            self._update_day_state(eq, now)

            if eq < self.min_equity_to_track:
                self.state.drawdown = 0.0
                self._update_halt_status(now)
                self._save_state()
                return self.state

            if self.state.peak_equity <= 0.0:
                self.state.peak_equity = eq
                self.state.drawdown = 0.0
                self._update_halt_status(now)
                self._save_state()
                return self.state

            if eq > self.state.peak_equity:
                self.state.peak_equity = eq

            peak = self.state.peak_equity
            dd = max(0.0, (peak - eq) / peak) if peak > 0.0 else 0.0

            self.state.drawdown = float(dd)

            if dd > self.state.max_drawdown_seen:
                self.state.max_drawdown_seen = float(dd)

            self._apply_drawdown_policy(now)
            self._save_state()
            return self.state

    # -----------------------------------------------------

    def can_trade(self, now_ts: Optional[float] = None) -> bool:
        with self._lock:
            now = float(now_ts if now_ts is not None else time.time())
            self._update_halt_status(now)
            self._save_state()
            return not self.state.halted

    # -----------------------------------------------------

    def register_execution_failure(self, now_ts: Optional[float] = None) -> None:
        with self._lock:
            now = float(now_ts if now_ts is not None else time.time())
            self.state.consecutive_failures += 1

            if self.max_consecutive_failures > 0 and self.state.consecutive_failures >= self.max_consecutive_failures:
                self._halt(
                    reason=f"max_consecutive_failures_exceeded ({self.state.consecutive_failures} >= {self.max_consecutive_failures})",
                    now_ts=now,
                )

            self._save_state()

    def register_execution_success(self) -> None:
        with self._lock:
            self.state.consecutive_failures = 0
            self._save_state()

    # -----------------------------------------------------

    def decision_gate(
        self,
        decision: Dict[str, Any],
        *,
        equity: float,
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            now = float(now_ts if now_ts is not None else time.time())

            self.update_equity(equity, now_ts=now)

            out = dict(decision or {})
            meta = dict(out.get("meta", {}) or {})

            meta["pair"] = self.pair
            meta["dd_peak_equity"] = float(self.state.peak_equity)
            meta["dd_equity"] = float(self.state.last_equity)
            meta["dd_drawdown"] = float(self.state.drawdown)
            meta["dd_max_seen"] = float(self.state.max_drawdown_seen)
            meta["dd_halted"] = bool(self.state.halted)
            meta["dd_halt_reason"] = self.state.halt_reason
            meta["dd_cooldown_until_ts"] = self.state.cooldown_until_ts
            meta["dd_daily_loss_pct"] = float(self.state.daily_loss_pct)
            meta["dd_consecutive_failures"] = int(self.state.consecutive_failures)

            if self.state.halted:
                out["side"] = "HOLD"
                out["amount"] = 0.0
                meta["risk_blocked"] = True
                meta["risk_block_reason"] = self.state.halt_reason or "drawdown_gate"

            out["meta"] = meta
            return out

    # -----------------------------------------------------

    def diagnostics(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "pair": self.pair,
                "peak_equity": float(self.state.peak_equity),
                "last_equity": float(self.state.last_equity),
                "drawdown": float(self.state.drawdown),
                "max_drawdown_seen": float(self.state.max_drawdown_seen),
                "daily_loss_pct": float(self.state.daily_loss_pct),
                "halted": bool(self.state.halted),
                "halt_reason": self.state.halt_reason,
                "cooldown_until_ts": self.state.cooldown_until_ts,
                "consecutive_failures": int(self.state.consecutive_failures),
                "max_drawdown_pct": float(self.max_drawdown_pct),
                "max_daily_loss_pct": float(self.max_daily_loss_pct),
                "cooldown_seconds": float(self.cooldown_seconds),
                "min_equity_to_track": float(self.min_equity_to_track),
                "max_consecutive_failures": int(self.max_consecutive_failures),
            }

    # -----------------------------------------------------
    # INTERNALS
    # -----------------------------------------------------

    def _apply_drawdown_policy(self, now_ts: float) -> None:
        self._update_halt_status(now_ts)

        if self.state.halted:
            return

        if self.max_drawdown_pct > 0 and self.state.drawdown >= self.max_drawdown_pct:
            self._halt(
                reason=f"max_drawdown_exceeded ({self.state.drawdown:.4f} >= {self.max_drawdown_pct:.4f})",
                now_ts=now_ts,
            )
            return

        if self.max_daily_loss_pct > 0 and self.state.daily_loss_pct >= self.max_daily_loss_pct:
            self._halt(
                reason=f"max_daily_loss_exceeded ({self.state.daily_loss_pct:.4f} >= {self.max_daily_loss_pct:.4f})",
                now_ts=now_ts,
            )

    def _update_day_state(self, equity: float, now_ts: float) -> None:
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now_ts))

        if self.state.last_day_key != day_key:
            self.state.last_day_key = day_key
            self.state.day_start_equity = max(float(equity or 0.0), 0.0)
            self.state.daily_loss_pct = 0.0

        start_eq = float(self.state.day_start_equity or 0.0)
        if start_eq > 0.0 and equity > 0.0:
            self.state.daily_loss_pct = max(0.0, (start_eq - equity) / start_eq)
        else:
            self.state.daily_loss_pct = 0.0

    def _halt(self, *, reason: str, now_ts: float) -> None:
        self.state.halted = True
        self.state.halt_reason = reason
        if self.cooldown_seconds > 0:
            self.state.cooldown_until_ts = float(now_ts + self.cooldown_seconds)
        else:
            self.state.cooldown_until_ts = None

    def _update_halt_status(self, now_ts: float) -> None:
        until = self.state.cooldown_until_ts
        if until is None:
            return

        if now_ts >= float(until):
            self.state.halted = False
            self.state.halt_reason = None
            self.state.cooldown_until_ts = None
            self.state.consecutive_failures = 0

    # -----------------------------------------------------
    # PERSISTENCE
    # -----------------------------------------------------

    def _load_state(self) -> None:
        with self._lock:
            if not self._state_file.exists():
                return

            try:
                payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            except Exception:
                return

            if not isinstance(payload, dict):
                return

            state = payload.get("state")
            if not isinstance(state, dict):
                return

            try:
                self.state = DrawdownState(
                    peak_equity=float(state.get("peak_equity") or 0.0),
                    last_equity=float(state.get("last_equity") or 0.0),
                    drawdown=float(state.get("drawdown") or 0.0),
                    max_drawdown_seen=float(state.get("max_drawdown_seen") or 0.0),
                    halted=bool(state.get("halted", False)),
                    halt_reason=state.get("halt_reason"),
                    cooldown_until_ts=float(state["cooldown_until_ts"]) if state.get("cooldown_until_ts") is not None else None,
                    last_day_key=state.get("last_day_key"),
                    day_start_equity=float(state.get("day_start_equity") or 0.0),
                    daily_loss_pct=float(state.get("daily_loss_pct") or 0.0),
                    consecutive_failures=int(state.get("consecutive_failures") or 0),
                )
            except Exception:
                self.state = DrawdownState()

    def _save_state(self) -> None:
        with self._lock:
            payload = {
                "pair": self.pair,
                "updated_ts": time.time(),
                "state": asdict(self.state),
                "config": {
                    "max_drawdown_pct": self.max_drawdown_pct,
                    "cooldown_seconds": self.cooldown_seconds,
                    "min_equity_to_track": self.min_equity_to_track,
                    "max_daily_loss_pct": self.max_daily_loss_pct,
                    "max_consecutive_failures": self.max_consecutive_failures,
                },
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )