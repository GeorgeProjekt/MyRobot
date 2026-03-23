from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


@dataclass
class WalkForwardWindow:
    start_ts: float
    end_ts: float
    trades: int
    wins: int
    losses: int
    pnl: float
    win_rate: float
    avg_pnl: float
    updated_ts: float


@dataclass
class WalkForwardSnapshot:
    pair: str
    total_windows: int
    latest_window: Optional[WalkForwardWindow]
    rolling_pnl: float
    rolling_win_rate: float
    rolling_avg_pnl: float
    total_trades: int
    updated_ts: float


@dataclass
class _TradeEvent:
    ts: float
    pnl: float


@dataclass
class _State:
    trades: List[_TradeEvent] = field(default_factory=list)
    updated_ts: float = 0.0


class WalkForwardTracker:
    """
    Persistent walk-forward performance tracker.

    Purpose:
    - maintain rolling trade history
    - aggregate trade results into fixed-size time windows
    - expose latest rolling stats for gating / diagnostics

    Notes:
    - this is not a backtester
    - it tracks realized trade outcomes only
    """

    def __init__(
        self,
        *,
        pair: str,
        window_seconds: float = 86400.0,
        max_windows: int = 30,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair).upper().strip()
        self.window_seconds = max(float(window_seconds), 60.0)
        self.max_windows = max(int(max_windows), 1)

        self._lock = RLock()
        self._state = _State()

        base_dir = Path(state_dir or (Path("runtime") / "walk_forward")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_walk_forward.json"

        self._load()

    # ---------------------------------------------------------

    def register_trade(self, pnl: float, ts: Optional[float] = None) -> None:
        with self._lock:
            event = _TradeEvent(
                ts=float(ts if ts is not None else time.time()),
                pnl=float(pnl or 0.0),
            )
            self._state.trades.append(event)
            self._state.updated_ts = time.time()
            self._prune()
            self._save()

    def snapshot(self) -> WalkForwardSnapshot:
        with self._lock:
            windows = self._build_windows()
            total_trades = len(self._state.trades)

            rolling_pnl = 0.0
            wins = 0
            avg_pnl = 0.0

            if total_trades > 0:
                pnls = [trade.pnl for trade in self._state.trades]
                rolling_pnl = float(sum(pnls))
                wins = sum(1 for x in pnls if x > 0.0)
                avg_pnl = rolling_pnl / total_trades

            rolling_win_rate = (wins / total_trades) if total_trades > 0 else 0.0
            latest = windows[-1] if windows else None

            return WalkForwardSnapshot(
                pair=self.pair,
                total_windows=len(windows),
                latest_window=latest,
                rolling_pnl=float(rolling_pnl),
                rolling_win_rate=float(rolling_win_rate),
                rolling_avg_pnl=float(avg_pnl),
                total_trades=total_trades,
                updated_ts=float(self._state.updated_ts or 0.0),
            )

    def latest_metrics(self) -> Dict[str, Any]:
        snap = self.snapshot()
        latest = snap.latest_window

        return {
            "pair": snap.pair,
            "rolling_pnl": snap.rolling_pnl,
            "rolling_win_rate": snap.rolling_win_rate,
            "rolling_avg_pnl": snap.rolling_avg_pnl,
            "total_trades": snap.total_trades,
            "window_seconds": self.window_seconds,
            "max_windows": self.max_windows,
            "latest_window": asdict(latest) if latest is not None else None,
            "updated_ts": snap.updated_ts,
        }

    def reset(self) -> None:
        with self._lock:
            self._state = _State(updated_ts=time.time())
            self._save()

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _prune(self) -> None:
        if not self._state.trades:
            return

        latest_ts = max(trade.ts for trade in self._state.trades)
        cutoff = latest_ts - (self.window_seconds * self.max_windows)

        self._state.trades = [
            trade for trade in self._state.trades
            if trade.ts >= cutoff
        ]

    def _build_windows(self) -> List[WalkForwardWindow]:
        if not self._state.trades:
            return []

        trades = sorted(self._state.trades, key=lambda x: x.ts)
        earliest_ts = trades[0].ts
        latest_ts = trades[-1].ts

        first_start = latest_ts - (self.window_seconds * self.max_windows)
        first_start = min(first_start, earliest_ts)

        windows: List[WalkForwardWindow] = []

        window_start = first_start
        while window_start <= latest_ts:
            window_end = window_start + self.window_seconds
            bucket = [trade for trade in trades if window_start <= trade.ts < window_end]

            trade_count = len(bucket)
            wins = sum(1 for trade in bucket if trade.pnl > 0.0)
            losses = sum(1 for trade in bucket if trade.pnl < 0.0)
            pnl = float(sum(trade.pnl for trade in bucket))
            win_rate = (wins / trade_count) if trade_count > 0 else 0.0
            avg_pnl = (pnl / trade_count) if trade_count > 0 else 0.0

            windows.append(
                WalkForwardWindow(
                    start_ts=float(window_start),
                    end_ts=float(window_end),
                    trades=trade_count,
                    wins=wins,
                    losses=losses,
                    pnl=pnl,
                    win_rate=float(win_rate),
                    avg_pnl=float(avg_pnl),
                    updated_ts=float(self._state.updated_ts or 0.0),
                )
            )

            window_start = window_end

        if len(windows) > self.max_windows:
            windows = windows[-self.max_windows:]

        return windows

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
                trades_raw = state.get("trades") or []
                trades: List[_TradeEvent] = []
                for row in trades_raw:
                    if not isinstance(row, dict):
                        continue
                    trades.append(
                        _TradeEvent(
                            ts=float(row.get("ts") or 0.0),
                            pnl=float(row.get("pnl") or 0.0),
                        )
                    )

                self._state = _State(
                    trades=trades,
                    updated_ts=float(state.get("updated_ts") or 0.0),
                )
                self._prune()
            except Exception:
                self._state = _State()

    def _save(self) -> None:
        with self._lock:
            payload = {
                "pair": self.pair,
                "window_seconds": self.window_seconds,
                "max_windows": self.max_windows,
                "updated_ts": time.time(),
                "state": {
                    "trades": [asdict(trade) for trade in self._state.trades],
                    "updated_ts": self._state.updated_ts,
                },
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )