from __future__ import annotations

from typing import Any, Dict


class TradeScorer:
    """
    Deterministic trade scoring module.

    Purpose:
    - score a normalized signal context into confidence-like range [0,1]
    - accept the real payload shape used by TradeDecisionAgent
    - remain stateless and pair-safe
    """

    def score(self, signal: Dict[str, Any], market_context: Dict[str, Any]) -> float:
        signal = signal if isinstance(signal, dict) else {}
        market_context = market_context if isinstance(market_context, dict) else {}

        if not signal:
            return 0.0

        side = str(signal.get("side") or signal.get("signal") or "").upper().strip()
        base_confidence = self._safe_float(signal.get("confidence"), 0.5)

        score = max(0.0, min(base_confidence, 1.0))

        trend = self._normalize_trend(
            market_context.get("trend")
            or market_context.get("direction")
            or market_context.get("market_trend")
        )

        volatility_regime = self._normalize_volatility(
            market_context.get("volatility_state")
            or market_context.get("volatility_regime")
            or market_context.get("regime")
            or market_context.get("volatility")
        )

        sentiment = str(
            market_context.get("sentiment")
            or market_context.get("market_sentiment")
            or ""
        ).lower().strip()

        # trend alignment
        if trend == "bullish" and side == "BUY":
            score += 0.15
        elif trend == "bearish" and side == "SELL":
            score += 0.15
        elif trend in {"bullish", "bearish"}:
            score -= 0.20

        # volatility regime
        if volatility_regime == "low":
            score += 0.05
        elif volatility_regime == "medium":
            score += 0.10
        elif volatility_regime == "high":
            score -= 0.10
        elif volatility_regime == "extreme":
            score -= 0.20

        # sentiment penalty / boost
        if sentiment in {"extreme_fear", "panic"}:
            score -= 0.20
        elif sentiment == "fear":
            score -= 0.10
        elif sentiment in {"greed", "bullish"}:
            score += 0.05

        return float(self._clip(score, 0.0, 1.0))

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
        return aliases.get(trend, trend)

    def _normalize_volatility(self, value: Any) -> str:
        raw = str(value or "").lower().strip()
        aliases = {
            "elevated": "medium",
            "normal": "medium",
            "spike": "extreme",
        }
        return aliases.get(raw, raw)

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))