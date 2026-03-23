from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


@dataclass
class PredictionRecord:
    direction: str
    confidence: float
    score: float
    ts: float


class AIPredictionMonitor:
    """
    Persistent deterministic prediction monitor.

    Purpose:
    - keep bounded history of prediction outputs
    - expose stable aggregate stats for dashboard / gating
    - never crash on malformed prediction payloads
    """

    def __init__(
        self,
        *,
        pair: Optional[str] = None,
        max_history: int = 200,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair or "GLOBAL").upper().strip()
        self.max_history = max(int(max_history), 1)

        self._lock = RLock()
        self.history: List[PredictionRecord] = []

        base_dir = Path(state_dir or (Path("runtime") / "prediction_monitor")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_prediction_monitor.json"

        self._load()

    def record(self, prediction: Dict[str, Any]) -> None:
        normalized = self._normalize_prediction(prediction)
        if normalized is None:
            return

        with self._lock:
            self.history.append(normalized)
            if len(self.history) > self.max_history:
                self.history = self.history[-self.max_history:]
            self._save()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            bulls = sum(1 for item in self.history if item.direction == "bullish")
            bears = sum(1 for item in self.history if item.direction == "bearish")
            neutrals = sum(1 for item in self.history if item.direction == "neutral")

            total = len(self.history)
            avg_confidence = (
                sum(item.confidence for item in self.history) / total if total > 0 else 0.0
            )
            avg_score = (
                sum(item.score for item in self.history) / total if total > 0 else 0.0
            )

            latest = asdict(self.history[-1]) if self.history else None

            return {
                "pair": self.pair,
                "total_predictions": total,
                "bull_predictions": bulls,
                "bear_predictions": bears,
                "neutral_predictions": neutrals,
                "avg_confidence": float(avg_confidence),
                "avg_score": float(avg_score),
                "latest": latest,
            }

    def reset(self) -> None:
        with self._lock:
            self.history = []
            self._save()

    # ---------------------------------------------------------

    def _normalize_prediction(self, prediction: Any) -> Optional[PredictionRecord]:
        if not isinstance(prediction, dict):
            return None

        direction = str(
            prediction.get("direction")
            or prediction.get("trend")
            or prediction.get("signal")
            or "neutral"
        ).lower().strip()

        aliases = {
            "bull": "bullish",
            "up": "bullish",
            "bear": "bearish",
            "down": "bearish",
            "flat": "neutral",
            "sideways": "neutral",
        }
        direction = aliases.get(direction, direction)
        if direction not in {"bullish", "bearish", "neutral"}:
            direction = "neutral"

        confidence = self._clip(self._safe_float(prediction.get("confidence"), 0.0), 0.0, 1.0)
        score = self._safe_float(prediction.get("score"), 0.0)
        ts = self._safe_float(prediction.get("ts"), time.time())

        return PredictionRecord(
            direction=direction,
            confidence=float(confidence),
            score=float(score),
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

            out: List[PredictionRecord] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                normalized = self._normalize_prediction(row)
                if normalized is not None:
                    out.append(normalized)

            self.history = out[-self.max_history:]

    def _save(self) -> None:
        with self._lock:
            payload = {
                "pair": self.pair,
                "max_history": self.max_history,
                "updated_ts": time.time(),
                "history": [asdict(item) for item in self.history[-self.max_history:]],
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )