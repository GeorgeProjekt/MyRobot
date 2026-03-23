from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


@dataclass
class DecisionLogRecord:
    pair: str
    side: str
    confidence: float
    strategy: Optional[str]
    reason: Optional[str]
    allowed: bool
    ts: float


class AIDecisionLogger:
    """
    Persistent deterministic decision logger.

    Purpose:
    - store normalized decision records for audit
    - keep bounded history
    - never fail on malformed payloads
    """

    def __init__(
        self,
        *,
        pair: Optional[str] = None,
        max_history: int = 1000,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair or "GLOBAL").upper().strip()
        self.max_history = max(int(max_history), 1)

        self._lock = RLock()
        self._history: List[DecisionLogRecord] = []

        base_dir = Path(state_dir or (Path("runtime") / "ai_decisions")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_decision_log.json"

        self._load()

    def log(self, decision: Dict[str, Any]) -> None:
        record = self._normalize(decision)
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
            buys = sum(1 for item in self._history if item.side == "BUY")
            sells = sum(1 for item in self._history if item.side == "SELL")
            holds = sum(1 for item in self._history if item.side == "HOLD")
            allowed = sum(1 for item in self._history if item.allowed)

            avg_conf = (
                sum(item.confidence for item in self._history) / total
                if total > 0 else 0.0
            )

            latest = asdict(self._history[-1]) if self._history else None

            return {
                "pair": self.pair,
                "total": total,
                "buy_count": buys,
                "sell_count": sells,
                "hold_count": holds,
                "allowed_count": allowed,
                "avg_confidence": float(avg_conf),
                "latest": latest,
            }

    def reset(self) -> None:
        with self._lock:
            self._history = []
            self._save()

    # ---------------------------------------------------------

    def _normalize(self, decision: Any) -> Optional[DecisionLogRecord]:
        if not isinstance(decision, dict):
            return None

        side = str(decision.get("side") or "HOLD").upper().strip()
        if side not in {"BUY", "SELL", "HOLD"}:
            side = "HOLD"

        meta = decision.get("meta")
        meta = meta if isinstance(meta, dict) else {}

        pair = str(
            decision.get("pair")
            or decision.get("symbol")
            or self.pair
        ).upper().strip()

        confidence = self._clip(self._safe_float(decision.get("confidence"), 0.0), 0.0, 1.0)
        strategy = decision.get("strategy")
        if strategy is None:
            strategy = meta.get("strategy")

        reason = (
            meta.get("risk_reason")
            or meta.get("reason")
            or decision.get("reason")
        )
        allowed = bool(meta.get("risk_allowed", side == "HOLD" or decision.get("allow_trade", True)))
        ts = self._safe_float(decision.get("ts"), time.time())

        return DecisionLogRecord(
            pair=pair,
            side=side,
            confidence=float(confidence),
            strategy=(str(strategy) if strategy not in (None, "") else None),
            reason=(str(reason) if reason not in (None, "") else None),
            allowed=bool(allowed),
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

            out: List[DecisionLogRecord] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
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