from __future__ import annotations

from typing import Any, Dict, List


class MarketPredictor:
    """
    Deterministic classical market predictor.

    Purpose:
    - derive one normalized directional prediction from structure/volatility context
    - never invent random confidence
    - remain robust on sparse or malformed input
    """

    def predict(self, market_structure: Dict[str, Any], volatility: Dict[str, Any]) -> Dict[str, Any]:
        structure = market_structure if isinstance(market_structure, dict) else {}
        vol = volatility if isinstance(volatility, dict) else {}

        trend = self._normalize_trend(
            structure.get("trend")
            or structure.get("direction")
            or structure.get("market_trend")
        )
        phase = str(
            structure.get("phase")
            or structure.get("regime")
            or ""
        ).lower().strip()

        volatility_state = self._normalize_volatility_state(
            vol.get("state")
            or vol.get("volatility_state")
            or vol.get("regime")
            or vol.get("value")
        )
        volatility_value = self._safe_float(
            vol.get("value", vol.get("volatility")),
            0.0,
        )

        direction = trend if trend in {"bullish", "bearish"} else "neutral"

        confidence = 0.50
        reasons: List[str] = []

        if trend == "bullish":
            confidence += 0.15
            reasons.append("trend_bullish")
        elif trend == "bearish":
            confidence += 0.15
            reasons.append("trend_bearish")
        else:
            reasons.append("trend_neutral")

        if phase in {"breakout", "markup", "markdown"}:
            confidence += 0.10
            reasons.append(f"phase_{phase}")
        elif phase in {"range", "distribution", "accumulation"}:
            confidence += 0.03
            reasons.append(f"phase_{phase}")

        if volatility_state == "low":
            confidence += 0.05
            reasons.append("volatility_low")
        elif volatility_state == "normal":
            confidence += 0.08
            reasons.append("volatility_normal")
        elif volatility_state == "high":
            confidence -= 0.08
            reasons.append("volatility_high")
        elif volatility_state == "extreme":
            confidence -= 0.18
            reasons.append("volatility_extreme")

        if direction == "neutral" and volatility_state in {"high", "extreme"}:
            confidence -= 0.05

        confidence = self._clip(confidence, 0.0, 1.0)

        score = 0.0
        if direction == "bullish":
            score = confidence
        elif direction == "bearish":
            score = -confidence

        return {
            "direction": direction,
            "trend": direction,
            "confidence": float(confidence),
            "score": float(score),
            "volatility_state": volatility_state,
            "volatility_value": float(volatility_value),
            "phase": phase or "undefined",
            "reason": ";".join(reasons) if reasons else "ok",
        }

    # ---------------------------------------------------------

    def _normalize_trend(self, value: Any) -> str:
        trend = str(value or "").lower().strip()
        aliases = {
            "bull": "bullish",
            "up": "bullish",
            "bear": "bearish",
            "down": "bearish",
            "flat": "neutral",
            "sideways": "neutral",
        }
        trend = aliases.get(trend, trend)
        if trend not in {"bullish", "bearish", "neutral"}:
            return "neutral"
        return trend

    def _normalize_volatility_state(self, value: Any) -> str:
        if isinstance(value, (int, float)):
            v = float(value)
            if v >= 0.08:
                return "extreme"
            if v >= 0.04:
                return "high"
            if v >= 0.015:
                return "normal"
            if v > 0.0:
                return "low"
            return "unknown"

        state = str(value or "").lower().strip()
        aliases = {
            "medium": "normal",
            "elevated": "high",
            "spike": "extreme",
        }
        state = aliases.get(state, state)
        if state not in {"low", "normal", "high", "extreme"}:
            return "unknown"
        return state

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))