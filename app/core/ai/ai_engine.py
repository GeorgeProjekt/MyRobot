from __future__ import annotations

from typing import Any, Dict


class AIEngine:
    """
    Deterministic fallback AI engine.

    Purpose:
    - replace placeholder output with stable, data-driven inference
    - never invent arbitrary values
    - provide a normalized analysis block usable by upper layers

    Input market_data may include:
    - price / close / last
    - history / closes / candles
    - volatility / atr / regime
    """

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        market_data = market_data if isinstance(market_data, dict) else {}

        closes = self._extract_closes(market_data)
        last_price = self._last_price(market_data, closes)

        if last_price <= 0.0:
            return {
                "regime": "no_data",
                "prediction": 0.0,
                "confidence": 0.0,
                "volatility_regime": "unknown",
                "trend": "neutral",
                "market_data_ok": False,
                "reason": "missing_price",
            }

        trend = self._infer_trend(closes, last_price)
        prediction = self._infer_prediction_pct(closes, last_price)
        volatility = self._infer_volatility(market_data, closes, last_price)
        volatility_regime = self._volatility_regime(volatility)
        regime = self._infer_regime(trend, volatility_regime)
        confidence = self._infer_confidence(trend, volatility_regime, closes)

        return {
            "regime": regime,
            "prediction": float(prediction),
            "confidence": float(confidence),
            "volatility_regime": volatility_regime,
            "trend": trend,
            "market_data_ok": True,
            "last_price": float(last_price),
            "sample_size": len(closes),
        }

    # ---------------------------------------------------------

    def _extract_closes(self, market_data: Dict[str, Any]) -> list[float]:
        closes = []

        direct = market_data.get("closes")
        if isinstance(direct, list):
            closes.extend(self._positive_floats(direct))

        history = market_data.get("history")
        if isinstance(history, list):
            for row in history:
                if isinstance(row, dict):
                    value = row.get("close", row.get("price"))
                    fv = self._safe_float(value, 0.0)
                    if fv > 0.0:
                        closes.append(fv)

        candles = market_data.get("candles")
        if isinstance(candles, list):
            for row in candles:
                if isinstance(row, dict):
                    value = row.get("close")
                    fv = self._safe_float(value, 0.0)
                    if fv > 0.0:
                        closes.append(fv)

        return closes

    def _last_price(self, market_data: Dict[str, Any], closes: list[float]) -> float:
        for key in ("price", "close", "last"):
            value = self._safe_float(market_data.get(key), 0.0)
            if value > 0.0:
                return value

        if closes:
            return float(closes[-1])

        return 0.0

    def _infer_trend(self, closes: list[float], last_price: float) -> str:
        if len(closes) < 5:
            return "neutral"

        short = self._mean(closes[-5:])
        long = self._mean(closes[-20:] if len(closes) >= 20 else closes)

        if short > long and last_price >= short:
            return "bullish"
        if short < long and last_price <= short:
            return "bearish"
        return "neutral"

    def _infer_prediction_pct(self, closes: list[float], last_price: float) -> float:
        if len(closes) < 2:
            return 0.0

        reference = closes[-5] if len(closes) >= 5 else closes[0]
        if reference <= 0.0:
            return 0.0

        return ((last_price - reference) / reference) * 100.0

    def _infer_volatility(self, market_data: Dict[str, Any], closes: list[float], last_price: float) -> float:
        atr = self._safe_float(market_data.get("atr"), 0.0)
        if atr > 0.0 and last_price > 0.0:
            return atr / last_price

        explicit = self._safe_float(market_data.get("volatility"), 0.0)
        if explicit > 0.0:
            return explicit

        if len(closes) < 3:
            return 0.0

        returns = []
        for i in range(1, len(closes)):
            prev_close = closes[i - 1]
            close = closes[i]
            if prev_close <= 0.0:
                continue
            returns.append((close / prev_close) - 1.0)

        if len(returns) < 2:
            return 0.0

        mean = self._mean(returns)
        variance = sum((x - mean) ** 2 for x in returns) / len(returns)
        return variance ** 0.5

    def _volatility_regime(self, volatility: float) -> str:
        if volatility >= 0.08:
            return "extreme"
        if volatility >= 0.04:
            return "high"
        if volatility >= 0.015:
            return "normal"
        if volatility > 0.0:
            return "low"
        return "unknown"

    def _infer_regime(self, trend: str, volatility_regime: str) -> str:
        if trend == "bullish":
            return "trend_up_high_vol" if volatility_regime in {"high", "extreme"} else "trend_up"
        if trend == "bearish":
            return "trend_down_high_vol" if volatility_regime in {"high", "extreme"} else "trend_down"
        return "range_high_vol" if volatility_regime in {"high", "extreme"} else "range"

    def _infer_confidence(self, trend: str, volatility_regime: str, closes: list[float]) -> float:
        base = 0.50

        if trend in {"bullish", "bearish"}:
            base += 0.15

        if volatility_regime == "normal":
            base += 0.10
        elif volatility_regime == "low":
            base += 0.05
        elif volatility_regime == "high":
            base -= 0.05
        elif volatility_regime == "extreme":
            base -= 0.15

        if len(closes) >= 20:
            base += 0.05

        return self._clip(base, 0.0, 1.0)

    def _positive_floats(self, values: list[Any]) -> list[float]:
        out: list[float] = []
        for value in values:
            fv = self._safe_float(value, 0.0)
            if fv > 0.0:
                out.append(fv)
        return out

    def _mean(self, values: list[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clip(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))