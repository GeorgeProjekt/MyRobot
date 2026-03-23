from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


@dataclass
class TradeMemoryRecord:
    pair: str
    side: str
    price: float
    amount: float
    pnl: float
    confidence: float
    strategy: Optional[str]
    regime: Optional[str]
    ts: float


class AITradeMemory:
    """
    Persistent deterministic trade memory.

    Purpose:
    - store bounded per-pair realized trade history
    - expose stable recent context to AI / scoring layers
    - never create synthetic trades or inferred pnl
    """

    def __init__(
        self,
        *,
        pair: Optional[str] = None,
        max_history: int = 2000,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair or "GLOBAL").upper().strip()
        self.max_history = max(int(max_history), 1)

        self._lock = RLock()
        self._history: List[TradeMemoryRecord] = []

        base_dir = Path(state_dir or (Path("runtime") / "ai_trade_memory")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_trade_memory.json"

        self._load()

    # ---------------------------------------------------------

    def remember(self, trade: Dict[str, Any]) -> None:
        record = self._normalize(trade)
        if record is None:
            return

        with self._lock:
            self._history.append(record)
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history:]
            self._save()

    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            n = max(int(limit), 1)
            return [asdict(item) for item in self._history[-n:]]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._history)
            total_pnl = sum(item.pnl for item in self._history)
            wins = sum(1 for item in self._history if item.pnl > 0.0)
            losses = sum(1 for item in self._history if item.pnl < 0.0)
            avg_pnl = (total_pnl / total) if total > 0 else 0.0
            avg_confidence = (
                sum(item.confidence for item in self._history) / total if total > 0 else 0.0
            )
            win_rate = (wins / total) if total > 0 else 0.0

            latest = asdict(self._history[-1]) if self._history else None

            strategies: Dict[str, Dict[str, float]] = {}
            for item in self._history:
                key = item.strategy or "unknown"
                bucket = strategies.setdefault(key, {"count": 0.0, "pnl": 0.0})
                bucket["count"] += 1.0
                bucket["pnl"] += float(item.pnl)

            return {
                "pair": self.pair,
                "total": total,
                "wins": wins,
                "losses": losses,
                "win_rate": float(win_rate),
                "total_pnl": float(total_pnl),
                "avg_pnl": float(avg_pnl),
                "avg_confidence": float(avg_confidence),
                "strategies": strategies,
                "latest": latest,
            }

    def reset(self) -> None:
        with self._lock:
            self._history = []
            self._save()

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _normalize(self, trade: Any) -> Optional[TradeMemoryRecord]:
        if not isinstance(trade, dict):
            return None

        pair = str(trade.get("pair") or trade.get("symbol") or self.pair).upper().strip()
        side = str(trade.get("side") or "HOLD").upper().strip()
        if side not in {"BUY", "SELL", "HOLD"}:
            side = "HOLD"

        price = self._safe_float(trade.get("price"), 0.0)
        amount = self._safe_float(trade.get("amount", trade.get("size")), 0.0)
        pnl = self._safe_float(trade.get("pnl"), 0.0)
        confidence = self._clip(self._safe_float(trade.get("confidence"), 0.0), 0.0, 1.0)

        strategy = trade.get("strategy")
        if strategy is None and isinstance(trade.get("meta"), dict):
            strategy = trade["meta"].get("strategy")

        regime = trade.get("regime")
        if regime is None and isinstance(trade.get("meta"), dict):
            regime = trade["meta"].get("regime")

        ts = self._safe_float(trade.get("ts"), time.time())

        return TradeMemoryRecord(
            pair=pair,
            side=side,
            price=float(price),
            amount=float(amount),
            pnl=float(pnl),
            confidence=float(confidence),
            strategy=(str(strategy) if strategy not in (None, "") else None),
            regime=(str(regime) if regime not in (None, "") else None),
            ts=float(ts),
        )

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

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

            rows = payload.get("history")
            if not isinstance(rows, list):
                return

            out: List[TradeMemoryRecord] = []
            for row in rows:
                normalized = self._normalize(row)
                if normalized is not None:
                    out.append(normalized)

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