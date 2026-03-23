from __future__ import annotations

from typing import Any, Dict, List


class DeepMarketPredictor:
    """
    Deterministic market predictor.

    Notes:
    - replaces misleading placeholder/deep naming behavior with a transparent momentum model
    - returns stable normalized payload
    - never crashes on short / malformed inputs
    """

    def predict(self, df: Any) -> Dict[str, Any]:
        closes = self._extract_closes(df)

        if len(closes) < 5:
            return {
                "trend": "neutral",
                "direction": "neutral",
                "confidence": 0.0,
                "score": 0.0,
                "prediction_pct": 0.0,
                "sample_size": len(closes),
                "reason": "insufficient_data",
            }

        returns = self._returns(closes)
        if not returns:
            return {
                "trend": "neutral",
                "direction": "neutral",
                "confidence": 0.0,
                "score": 0.0,
                "prediction_pct": 0.0,
                "sample_size": len(closes),
                "reason": "invalid_returns",
            }

        window = returns[-10:] if len(returns) >= 10 else returns
        momentum_score = sum(window) / len(window)

        direction = "neutral"
        if momentum_score > 0.002:
            direction = "bullish"
        elif momentum_score < -0.002:
            direction = "bearish"

        confidence = min(abs(momentum_score) * 100.0, 1.0)
        prediction_pct = momentum_score * 100.0

        return {
            "trend": direction,
            "direction": direction,
            "confidence": float(confidence),
            "score": float(momentum_score),
            "prediction_pct": float(prediction_pct),
            "sample_size": len(closes),
            "reason": "ok",
        }

    # ---------------------------------------------------------

    def _extract_closes(self, df: Any) -> List[float]:
        if df is None:
            return []

        # pandas-like DataFrame
        if hasattr(df, "__getitem__"):
            try:
                close_col = df["close"]
                if hasattr(close_col, "tolist"):
                    return [float(v) for v in close_col.tolist() if self._is_positive(v)]
            except Exception:
                pass

        # dict payload
        if isinstance(df, dict):
            for key in ("close", "closes", "prices"):
                value = df.get(key)
                if isinstance(value, list):
                    return [float(v) for v in value if self._is_positive(v)]

        # direct iterable
        if isinstance(df, list):
            if df and isinstance(df[0], dict):
                out: List[float] = []
                for row in df:
                    value = row.get("close") if isinstance(row, dict) else None
                    if self._is_positive(value):
                        out.append(float(value))
                return out
            return [float(v) for v in df if self._is_positive(v)]

        return []

    def _returns(self, closes: List[float]) -> List[float]:
        out: List[float] = []
        for i in range(1, len(closes)):
            prev_close = closes[i - 1]
            close = closes[i]
            if prev_close <= 0.0:
                continue
            out.append((close / prev_close) - 1.0)
        return out

    def _is_positive(self, value: Any) -> bool:
        try:
            return float(value) > 0.0
        except Exception:
            return False