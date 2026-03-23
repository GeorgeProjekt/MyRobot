from __future__ import annotations

from typing import Any, Dict


class OrderflowIntelligence:
    """
    Deterministic orderflow intelligence layer.

    Purpose:
    - consume normalized orderflow analysis
    - derive stable bias / pressure / execution notes
    - avoid brittle assumptions about missing keys
    """

    def evaluate(self, orderflow: Dict[str, Any]) -> Dict[str, Any]:
        payload = orderflow if isinstance(orderflow, dict) else {}

        imbalance = self._safe_float(payload.get("imbalance"), 0.0)
        spread_pct = self._safe_float(payload.get("spread_pct"), 0.0)
        bid_volume = self._safe_float(payload.get("bid_volume"), 0.0)
        ask_volume = self._safe_float(payload.get("ask_volume"), 0.0)
        liquidity = self._safe_float(payload.get("liquidity"), bid_volume + ask_volume)
        bias = str(payload.get("bias") or "").lower().strip()

        if not bias:
            bias = self._bias_from_imbalance(imbalance)

        pressure = self._pressure_from_imbalance(imbalance)
        execution_regime = self._execution_regime(spread_pct=spread_pct, liquidity=liquidity)

        return {
            "bias": bias,
            "pressure": pressure,
            "execution_regime": execution_regime,
            "imbalance": float(max(-1.0, min(1.0, imbalance))),
            "spread_pct": float(spread_pct),
            "bid_volume": float(bid_volume),
            "ask_volume": float(ask_volume),
            "liquidity": float(liquidity),
            "market_data_ok": bool(liquidity > 0.0 or spread_pct > 0.0),
        }

    # ---------------------------------------------------------

    def _bias_from_imbalance(self, imbalance: float) -> str:
        if imbalance >= 0.35:
            return "aggressive_buy"
        if imbalance >= 0.10:
            return "buy_imbalance"
        if imbalance <= -0.35:
            return "aggressive_sell"
        if imbalance <= -0.10:
            return "sell_imbalance"
        return "neutral"

    def _pressure_from_imbalance(self, imbalance: float) -> str:
        if imbalance >= 0.50:
            return "very_strong_buy"
        if imbalance >= 0.20:
            return "strong_buy"
        if imbalance > 0.05:
            return "mild_buy"
        if imbalance <= -0.50:
            return "very_strong_sell"
        if imbalance <= -0.20:
            return "strong_sell"
        if imbalance < -0.05:
            return "mild_sell"
        return "balanced"

    def _execution_regime(self, *, spread_pct: float, liquidity: float) -> str:
        if liquidity <= 0.0:
            return "unavailable"

        if spread_pct >= 0.01:
            return "poor"
        if spread_pct >= 0.004:
            return "wide"
        if spread_pct > 0.0:
            return "tradable"
        return "tight"

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default