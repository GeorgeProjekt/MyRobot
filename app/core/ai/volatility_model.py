from __future__ import annotations

from typing import Any, Dict, List


class VolatilityModel:
    """
    Deterministic volatility forecaster.

    Purpose:
    - derive one normalized volatility payload from market data
    - avoid random or placeholder outputs
    - stay robust on sparse or malformed inputs
    """

    def forecast(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        market_data = market_data if isinstance(market_data, dict) else {}

        closes = self._extract_closes(market_data)
        last_price = self._resolve_last_price(market_data, closes)

        explicit_atr = self._safe_float(market_data.get("atr"), 0.0)
        explicit_vol = self._safe_float(market_data.get("volatility"), 0.0)

        atr_value = 0.0
        if explicit_atr > 0.0:
            atr_value = explicit_atr
        elif len(closes) >= 2:
            atr_value = self._approx_atr_from_closes(closes)

        if explicit_vol > 0.0:
            rel_vol = explicit_vol
        elif last_price > 0.0 and atr_value > 0.0:
            rel_vol = atr_value / last_price
        else:
            rel_vol = self._returns_volatility(closes)

        state = self._state_from_value(rel_vol)

        return {
            "value": float(rel_vol),
            "atr": float(atr_value),
            "state": state,
            "sample_size": len(closes),
            "market_data_ok": bool(last_price > 0.0),
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

    def _approx_atr_from_closes(self, closes: List[float]) -> float:
        if len(closes) < 2:
            return 0.0

        diffs: List[float] = []
        for i in range(1, len(closes)):
            prev_close = closes[i - 1]
            close = closes[i]
            if prev_close <= 0.0 or close <= 0.0:
                continue
            diffs.append(abs(close - prev_close))

        if not diffs:
            return 0.0

        window = diffs[-14:] if len(diffs) >= 14 else diffs
        return sum(window) / len(window)

    def _returns_volatility(self, closes: List[float]) -> float:
        if len(closes) < 3:
            return 0.0

        returns: List[float] = []
        for i in range(1, len(closes)):
            prev_close = closes[i - 1]
            close = closes[i]
            if prev_close <= 0.0:
                continue
            returns.append((close / prev_close) - 1.0)

        if len(returns) < 2:
            return 0.0

        mean = sum(returns) / len(returns)
        variance = sum((x - mean) ** 2 for x in returns) / len(returns)
        return variance ** 0.5

    def _state_from_value(self, value: float) -> str:
        if value >= 0.08:
            return "extreme"
        if value >= 0.04:
            return "high"
        if value >= 0.015:
            return "normal"
        if value > 0.0:
            return "low"
        return "unknown"

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