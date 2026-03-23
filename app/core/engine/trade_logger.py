from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


@dataclass
class TradeLogRecord:
    pair: str
    side: str
    price: float
    amount: float
    status: str
    execution_ok: bool
    order_id: Optional[str]
    strategy: Optional[str]
    confidence: float
    risk_modifier: float
    ts: float


class TradeLogger:
    """
    Pair-isolated persistent trade logger.

    Responsibilities:
    - store normalized trade execution records
    - keep bounded history
    - expose recent trades and aggregate stats
    - never fail on malformed payloads
    """

    def __init__(
        self,
        pair: str,
        *,
        max_history: int = 5000,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair).upper().strip()
        self.max_history = max(int(max_history), 1)

        self._lock = RLock()
        self._history: List[TradeLogRecord] = []

        base_dir = Path(state_dir or (Path("runtime") / "trade_logs")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_trade_log.json"

        self._load()

    # ---------------------------------------------------------

    def log(self, trade: Dict[str, Any]) -> None:
        record = self._normalize(trade)
        if record is None:
            return

        with self._lock:
            self._history.append(record)
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history:]
            self._save()

    def record(self, trade: Dict[str, Any]) -> None:
        self.log(trade)

    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            n = max(int(limit), 1)
            return [asdict(item) for item in self._history[-n:]]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._history)
            buys = sum(1 for item in self._history if item.side == "BUY")
            sells = sum(1 for item in self._history if item.side == "SELL")
            ok_count = sum(1 for item in self._history if item.execution_ok)

            avg_conf = (
                sum(item.confidence for item in self._history) / total
                if total > 0 else 0.0
            )
            avg_risk_modifier = (
                sum(item.risk_modifier for item in self._history) / total
                if total > 0 else 0.0
            )

            latest = asdict(self._history[-1]) if self._history else None

            return {
                "pair": self.pair,
                "total": total,
                "buy_count": buys,
                "sell_count": sells,
                "execution_ok_count": ok_count,
                "execution_ok_rate": (ok_count / total) if total > 0 else 0.0,
                "avg_confidence": float(avg_conf),
                "avg_risk_modifier": float(avg_risk_modifier),
                "latest": latest,
            }

    def reset(self) -> None:
        with self._lock:
            self._history = []
            self._save()

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _normalize(self, trade: Any) -> Optional[TradeLogRecord]:
        if not isinstance(trade, dict):
            return None

        pair = str(trade.get("pair") or trade.get("symbol") or self.pair).upper().strip()
        if pair != self.pair:
            return None

        side = str(trade.get("side") or "HOLD").upper().strip()
        aliases = {
            "LONG": "BUY",
            "SHORT": "SELL",
            "BULLISH": "BUY",
            "BEARISH": "SELL",
        }
        side = aliases.get(side, side)
        if side not in {"BUY", "SELL", "HOLD"}:
            side = "HOLD"

        price = self._safe_float(trade.get("price"), 0.0)
        amount = self._safe_float(trade.get("amount", trade.get("size")), 0.0)
        status = str(trade.get("status") or "unknown").lower().strip()
        execution_ok = bool(trade.get("execution_ok", False))
        order_id = trade.get("order_id")
        strategy = trade.get("strategy")
        confidence = self._clip(self._safe_float(trade.get("confidence"), 0.0), 0.0, 1.0)
        risk_modifier = self._safe_float(trade.get("risk_modifier"), 1.0)
        ts = self._safe_float(trade.get("ts"), time.time())

        return TradeLogRecord(
            pair=pair,
            side=side,
            price=float(price),
            amount=float(amount),
            status=status,
            execution_ok=execution_ok,
            order_id=(str(order_id) if order_id not in (None, "") else None),
            strategy=(str(strategy) if strategy not in (None, "") else None),
            confidence=float(confidence),
            risk_modifier=float(risk_modifier),
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

            out: List[TradeLogRecord] = []
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