from __future__ import annotations

from typing import Any, Dict, List

import numpy as np


class LSTMPredictor:
    def __init__(self, window: int = 20):
        self.window = int(window or 20)

    def _normalize_series(self, series: Any) -> List[float]:
        out: List[float] = []
        for value in list(series or []):
            try:
                num = float(value)
            except Exception:
                continue
            if np.isfinite(num):
                out.append(num)
        return out

    def predict(self, series: Any) -> Dict[str, Any]:
        values = self._normalize_series(series)
        if len(values) < self.window:
            return {"direction": "neutral", "confidence": 0.0, "rows": len(values)}
        recent = np.asarray(values[-self.window:], dtype=float)
        diffs = np.diff(recent)
        momentum = float(np.mean(diffs)) if diffs.size else 0.0
        direction = "neutral"
        if momentum > 0:
            direction = "bullish"
        elif momentum < 0:
            direction = "bearish"
        baseline = float(np.mean(np.abs(recent))) if recent.size else 0.0
        confidence = abs(momentum) / baseline if baseline > 0 else 0.0
        confidence = max(0.0, min(confidence, 1.0))
        return {"direction": direction, "confidence": confidence, "rows": len(values), "momentum": momentum}

    def predict_from_features(self, features: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(features, dict):
            return self.predict([])
        result = self.predict(features.get("close") or [])
        result["feature_rows"] = int(features.get("rows") or 0)
        return result
