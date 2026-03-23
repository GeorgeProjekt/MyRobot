from __future__ import annotations

from typing import Any, Dict, List


class MarketStructure:
    """
    Deterministic market structure analyzer.

    Purpose:
    - derive normalized trend / phase / support / resistance from close prices
    - avoid placeholder outputs and random labels
    - stay robust on sparse or malformed input
    """

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        market_data = market_data if isinstance(market_data, dict) else {}

        closes = self._extract_closes(market_data)
        last_price = self._resolve_last_price(market_data, closes)

        if last_price <= 0.0 or len(closes) < 5:
            return {
                "trend": "neutral",
                "phase": "undefined",
                "support": 0.0,
                "resistance": 0.0,
                "sample_size": len(closes),
                "market_data_ok": False,
            }

        short_window = closes[-5:]
        medium_window = closes[-20:] if len(closes) >= 20 else closes

        short_ma = self._mean(short_window)
        medium_ma = self._mean(medium_window)

        trend = "neutral"
        if short_ma > medium_ma and last_price >= short_ma:
            trend = "bullish"
        elif short_ma < medium_ma and last_price <= short_ma:
            trend = "bearish"

        recent = closes[-20:] if len(closes) >= 20 else closes
        support = min(recent) if recent else 0.0
        resistance = max(recent) if recent else 0.0

        phase = self._infer_phase(
            trend=trend,
            last_price=last_price,
            support=support,
            resistance=resistance,
        )

        return {
            "trend": trend,
            "phase": phase,
            "support": float(support),
            "resistance": float(resistance),
            "short_ma": float(short_ma),
            "medium_ma": float(medium_ma),
            "sample_size": len(closes),
            "market_data_ok": True,
        }

    # ---------------------------------------------------------

    def _extract_closes(self, market_data: Dict[str, Any]) -> List[float]:
        closes: List[float] = []

        direct = market_data.get("closes")
        if isinstance(direct, list):
            closes.extend([float(v) for v in direct if self._is_positive(v)])

        candles = market_data.get("candles")
        if isinstance(candles, list):
            for row in candles:
                if isinstance(row, dict):
                    value = row.get("close")
                    if self._is_positive(value):
                        closes.append(float(value))

        history = market_data.get("history")
        if isinstance(history, list):
            for row in history:
                if isinstance(row, dict):
                    value = row.get("close", row.get("price"))
                    if self._is_positive(value):
                        closes.append(float(value))

        return closes

    def _resolve_last_price(self, market_data: Dict[str, Any], closes: List[float]) -> float:
        for key in ("price", "close", "last"):
            value = self._safe_float(market_data.get(key), 0.0)
            if value > 0.0:
                return value

        if closes:
            return float(closes[-1])

        return 0.0

    def _infer_phase(self, *, trend: str, last_price: float, support: float, resistance: float) -> str:
        if support <= 0.0 or resistance <= 0.0 or resistance < support:
            return "undefined"

        range_size = resistance - support
        if range_size <= 0.0:
            return "undefined"

        pos = (last_price - support) / range_size

        if trend == "bullish":
            if pos >= 0.80:
                return "breakout"
            return "markup"

        if trend == "bearish":
            if pos <= 0.20:
                return "breakdown"
            return "markdown"

        if pos <= 0.30:
            return "accumulation"
        if pos >= 0.70:
            return "distribution"
        return "range"

    def _mean(self, values: List[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def _is_positive(self, value: Any) -> bool:
        try:
            return float(value) > 0.0
        except Exception:
            return False