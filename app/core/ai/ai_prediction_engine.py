from __future__ import annotations

from typing import Any, Dict

from app.core.ai.ml_pipeline import MLPipeline
from app.core.ai.ml_signal_filter import MLSignalFilter


class AIPredictionEngine:
    """
    Deterministic prediction engine.

    Responsibilities:
    - run ML pipeline once
    - normalize prediction payload
    - evaluate incoming signal through MLSignalFilter
    - return stable shape for upper layers
    """

    def __init__(self, pair: str | None = None):
        self.pair = str(pair).upper().strip() if pair else None

        self.pipeline = MLPipeline()
        self.filter = MLSignalFilter(pair=self.pair, allow_missing_prediction=False)

    def process(self, df: Any, signals: Dict[str, Any]) -> Dict[str, Any]:
        prediction_raw = self._safe_dict(self.pipeline.run(df))
        prediction = self._normalize_prediction(prediction_raw)

        signal_payload = self._normalize_signal(signals)
        market_context = {"prediction": prediction}

        allowed = self.filter.filter(signal_payload, market_context)

        return {
            "prediction": prediction,
            "signal": signal_payload,
            "signal_allowed": bool(allowed),
            "signals": signal_payload if allowed else {},
        }

    # -----------------------------------------------------

    def _normalize_prediction(self, prediction: Dict[str, Any]) -> Dict[str, Any]:
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

        confidence = self._clip(
            self._safe_float(prediction.get("confidence"), 0.5),
            0.0,
            1.0,
        )

        return {
            "direction": direction,
            "confidence": confidence,
            "raw": prediction,
        }

    def _normalize_signal(self, signals: Dict[str, Any]) -> Dict[str, Any]:
        signal = self._safe_dict(signals)

        pair = str(signal.get("pair") or self.pair or "").upper().strip()
        side = str(signal.get("side") or signal.get("signal") or "").upper().strip()

        aliases = {
            "LONG": "BUY",
            "SHORT": "SELL",
            "BULLISH": "BUY",
            "BEARISH": "SELL",
        }
        side = aliases.get(side, side)

        confidence = self._clip(
            self._safe_float(signal.get("confidence"), 0.5),
            0.0,
            1.0,
        )

        return {
            "pair": pair,
            "side": side,
            "confidence": confidence,
            "raw": signal,
        }

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))