from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Optional, Dict, Any, List


@dataclass
class PerformanceRecord:
    pnl: float
    ts: float


@dataclass
class PerformanceSnapshot:
    trades: int
    pnl: float
    avg_trade: float
    wins: int
    losses: int
    win_rate: float
    updated_ts: float


class PerformanceMonitor:
    """
    Persistent deterministic performance monitor.

    Responsibilities:
    - store realized trade pnl events
    - expose aggregate performance stats
    - survive process restart
    """

    def __init__(
        self,
        *,
        pair: Optional[str] = None,
        max_history: int = 5000,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair or "GLOBAL").upper().strip()
        self.max_history = max(int(max_history), 1)

        self._lock = RLock()
        self._history: List[PerformanceRecord] = []

        base_dir = Path(state_dir or (Path("runtime") / "performance")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_performance.json"

        self._load()

    def record(self, profit: float, ts: Optional[float] = None) -> None:
        record = PerformanceRecord(
            pnl=float(profit or 0.0),
            ts=float(ts if ts is not None else time.time()),
        )

        with self._lock:
            self._history.append(record)
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history:]
            self._save()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            trades = len(self._history)
            pnl = sum(item.pnl for item in self._history)
            wins = sum(1 for item in self._history if item.pnl > 0.0)
            losses = sum(1 for item in self._history if item.pnl < 0.0)
            avg_trade = pnl / trades if trades > 0 else 0.0
            win_rate = wins / trades if trades > 0 else 0.0

            snapshot = PerformanceSnapshot(
                trades=trades,
                pnl=float(pnl),
                avg_trade=float(avg_trade),
                wins=wins,
                losses=losses,
                win_rate=float(win_rate),
                updated_ts=time.time(),
            )

            out = asdict(snapshot)
            out["pair"] = self.pair
            out["latest"] = asdict(self._history[-1]) if self._history else None
            return out

    def reset(self) -> None:
        with self._lock:
            self._history = []
            self._save()

    def _load(self) -> None:
        with self._lock:
            if not self._state_file.exists():
                return

            try:
                payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            except Exception:
                return

            rows = payload.get("history")
            if not isinstance(rows, list):
                return

            out: List[PerformanceRecord] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    out.append(
                        PerformanceRecord(
                            pnl=float(row.get("pnl") or 0.0),
                            ts=float(row.get("ts") or 0.0),
                        )
                    )
                except Exception:
                    continue

            self._history = out[-self.max_history:]

    def _save(self) -> None:
        with self._lock:
            payload = {
                "pair": self.pair,
                "max_history": self.max_history,
                "updated_ts": time.time(),
                "history": [asdict(item) for item in self._history[-self.max_history:]],
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )