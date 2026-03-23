from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


@dataclass
class FeedbackRecord:
    pair: str
    side: str
    confidence: float
    pnl: float
    success: bool
    strategy: Optional[str]
    ts: float


@dataclass
class FeedbackSnapshot:
    pair: str
    total_records: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    avg_confidence: float
    updated_ts: float


class AIPerformanceFeedbackEngine:
    """
    Persistent deterministic performance feedback engine.

    Purpose:
    - log realized trade outcomes for one pair
    - expose stable aggregate feedback for adaptive modules
    - never invent learning updates or mutate strategies implicitly
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
        self._history: List[FeedbackRecord] = []

        base_dir = Path(state_dir or (Path("runtime") / "ai_feedback")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_feedback.json"

        self._load()

    # ---------------------------------------------------------

    def record(self, payload: Dict[str, Any]) -> None:
        record = self._normalize_record(payload)
        if record is None:
            return

        with self._lock:
            self._history.append(record)
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history:]
            self._save()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._history)
            wins = sum(1 for item in self._history if item.success)
            losses = sum(1 for item in self._history if not item.success)
            total_pnl = sum(item.pnl for item in self._history)
            avg_pnl = (total_pnl / total) if total > 0 else 0.0
            avg_conf = (sum(item.confidence for item in self._history) / total) if total > 0 else 0.0
            win_rate = (wins / total) if total > 0 else 0.0

            snap = FeedbackSnapshot(
                pair=self.pair,
                total_records=total,
                wins=wins,
                losses=losses,
                win_rate=float(win_rate),
                total_pnl=float(total_pnl),
                avg_pnl=float(avg_pnl),
                avg_confidence=float(avg_conf),
                updated_ts=time.time(),
            )

            out = asdict(snap)
            out["latest"] = asdict(self._history[-1]) if self._history else None
            return out

    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            n = max(int(limit), 1)
            return [asdict(item) for item in self._history[-n:]]

    def feedback_signal(self) -> Dict[str, Any]:
        snap = self.snapshot()

        win_rate = float(snap["win_rate"])
        avg_pnl = float(snap["avg_pnl"])
        avg_confidence = float(snap["avg_confidence"])

        confidence_multiplier = 1.0
        risk_multiplier = 1.0
        state = "neutral"

        if snap["total_records"] >= 10:
            if win_rate >= 0.60 and avg_pnl > 0.0:
                confidence_multiplier = 1.10
                risk_multiplier = 1.05
                state = "positive"
            elif win_rate < 0.45 or avg_pnl < 0.0:
                confidence_multiplier = 0.85
                risk_multiplier = 0.80
                state = "negative"

        return {
            "pair": self.pair,
            "state": state,
            "confidence_multiplier": float(confidence_multiplier),
            "risk_multiplier": float(risk_multiplier),
            "win_rate": float(win_rate),
            "avg_pnl": float(avg_pnl),
            "avg_confidence": float(avg_confidence),
            "sample_size": int(snap["total_records"]),
        }

    def reset(self) -> None:
        with self._lock:
            self._history = []
            self._save()

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _normalize_record(self, payload: Any) -> Optional[FeedbackRecord]:
        if not isinstance(payload, dict):
            return None

        pair = str(payload.get("pair") or payload.get("symbol") or self.pair).upper().strip()
        side = str(payload.get("side") or "HOLD").upper().strip()
        if side not in {"BUY", "SELL", "HOLD"}:
            side = "HOLD"

        confidence = self._clip(self._safe_float(payload.get("confidence"), 0.0), 0.0, 1.0)
        pnl = self._safe_float(payload.get("pnl"), 0.0)
        success = bool(payload.get("success", pnl > 0.0))
        strategy = payload.get("strategy")
        strategy = str(strategy) if strategy not in (None, "") else None
        ts = self._safe_float(payload.get("ts"), time.time())

        return FeedbackRecord(
            pair=pair,
            side=side,
            confidence=float(confidence),
            pnl=float(pnl),
            success=bool(success),
            strategy=strategy,
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

            out: List[FeedbackRecord] = []
            for row in rows:
                normalized = self._normalize_record(row)
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