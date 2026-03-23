from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app.core.ai.feature_engineering import FeatureEngineering
from app.core.ai.lstm_predictor import LSTMPredictor


class MLPipeline:
    """
    Deterministic ML pipeline wrapper.

    Responsibilities:
    - build features safely
    - extract usable numeric sequence
    - call predictor once
    - normalize output into stable prediction payload
    """

    def __init__(self) -> None:
        self.features = FeatureEngineering()
        self.model = LSTMPredictor()

    def run(self, df: Any) -> Dict[str, Any]:
        features = self._safe_build_features(df)
        sequence = self._extract_sequence(features)

        if not sequence:
            return {
                "direction": "neutral",
                "confidence": 0.0,
                "raw": None,
                "reason": "empty_feature_sequence",
            }

        raw_prediction = self._safe_predict(sequence)
        return self._normalize_prediction(raw_prediction)

    # ---------------------------------------------------------

    def _safe_build_features(self, df: Any) -> Dict[str, Any]:
        try:
            built = self.features.build(df)
        except Exception:
            return {}

        return built if isinstance(built, dict) else {}

    def _extract_sequence(self, features: Dict[str, Any]) -> List[float]:
        if not features:
            return []

        candidate = features.get("return")
        if candidate is None:
            candidate = features.get("returns")

        values: List[float] = []

        # pandas Series / numpy-like with .values
        if hasattr(candidate, "values"):
            try:
                candidate = candidate.values
            except Exception:
                pass

        if isinstance(candidate, dict):
            iterable: Iterable[Any] = candidate.values()
        elif isinstance(candidate, (list, tuple)):
            iterable = candidate
        elif hasattr(candidate, "__iter__") and not isinstance(candidate, (str, bytes)):
            iterable = list(candidate)
        else:
            iterable = []

        for item in iterable:
            fv = self._safe_float(item, None)
            if fv is None:
                continue
            values.append(float(fv))

        return values

    def _safe_predict(self, sequence: List[float]) -> Any:
        try:
            return self.model.predict(sequence)
        except Exception:
            return None

    def _normalize_prediction(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            direction = str(
                raw.get("direction")
                or raw.get("trend")
                or raw.get("signal")
                or "neutral"
            ).lower().strip()

            confidence = self._clip(self._safe_float(raw.get("confidence"), 0.5), 0.0, 1.0)
            probability = self._safe_float(raw.get("probability"), confidence)
            score = self._safe_float(raw.get("score"), 0.0)

            direction = self._normalize_direction(direction)

            return {
                "direction": direction,
                "confidence": confidence,
                "probability": self._clip(probability, 0.0, 1.0),
                "score": score,
                "raw": raw,
            }

        value = self._safe_float(raw, None)
        if value is None:
            return {
                "direction": "neutral",
                "confidence": 0.0,
                "raw": raw,
                "reason": "invalid_model_output",
            }

        if value > 0:
            direction = "bullish"
        elif value < 0:
            direction = "bearish"
        else:
            direction = "neutral"

        confidence = self._clip(abs(float(value)), 0.0, 1.0)

        return {
            "direction": direction,
            "confidence": confidence,
            "probability": confidence,
            "score": float(value),
            "raw": raw,
        }

    def _normalize_direction(self, value: str) -> str:
        aliases = {
            "bull": "bullish",
            "up": "bullish",
            "bear": "bearish",
            "down": "bearish",
            "flat": "neutral",
            "sideways": "neutral",
        }
        normalized = aliases.get(value, value)
        if normalized not in {"bullish", "bearish", "neutral"}:
            return "neutral"
        return normalized

    def _safe_float(self, value: Any, default: Any = 0.0) -> Any:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))