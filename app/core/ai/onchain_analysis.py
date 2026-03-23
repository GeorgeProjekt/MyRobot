from __future__ import annotations

from typing import Any, Dict, List


class OnchainAnalysis:
    """
    Deterministic on-chain analysis layer.

    Purpose:
    - normalize on-chain style inputs into one stable payload
    - avoid fake blockchain metrics when data is missing
    - expose simple states usable by AI/risk layers
    """

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        market_data = market_data if isinstance(market_data, dict) else {}

        onchain = self._extract_onchain_payload(market_data)

        active_addresses = self._safe_float(onchain.get("active_addresses"), 0.0)
        tx_volume = self._safe_float(onchain.get("tx_volume"), 0.0)
        exchange_inflow = self._safe_float(onchain.get("exchange_inflow"), 0.0)
        exchange_outflow = self._safe_float(onchain.get("exchange_outflow"), 0.0)
        whale_transfers = self._safe_float(onchain.get("whale_transfers"), 0.0)
        netflow = exchange_outflow - exchange_inflow

        activity_state = self._activity_state(
            active_addresses=active_addresses,
            tx_volume=tx_volume,
        )
        flow_state = self._flow_state(netflow=netflow)
        whale_state = self._whale_state(whale_transfers=whale_transfers)

        bias = self._combine_bias(
            activity_state=activity_state,
            flow_state=flow_state,
            whale_state=whale_state,
        )

        return {
            "active_addresses": float(active_addresses),
            "tx_volume": float(tx_volume),
            "exchange_inflow": float(exchange_inflow),
            "exchange_outflow": float(exchange_outflow),
            "netflow": float(netflow),
            "whale_transfers": float(whale_transfers),
            "activity_state": activity_state,
            "flow_state": flow_state,
            "whale_state": whale_state,
            "bias": bias,
            "market_data_ok": bool(onchain),
        }

    # ---------------------------------------------------------

    def _extract_onchain_payload(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("onchain", "onchain_data", "chain", "blockchain"):
            value = market_data.get(key)
            if isinstance(value, dict):
                return value

        # allow direct payload too
        known_keys = {
            "active_addresses",
            "tx_volume",
            "exchange_inflow",
            "exchange_outflow",
            "whale_transfers",
        }
        if any(key in market_data for key in known_keys):
            return market_data

        return {}

    def _activity_state(self, *, active_addresses: float, tx_volume: float) -> str:
        if active_addresses <= 0.0 and tx_volume <= 0.0:
            return "unknown"

        if active_addresses > 100000 or tx_volume > 1_000_000:
            return "high_activity"

        if active_addresses > 10000 or tx_volume > 100000:
            return "normal_activity"

        return "low_activity"

    def _flow_state(self, *, netflow: float) -> str:
        if netflow > 0:
            return "bullish_outflow"
        if netflow < 0:
            return "bearish_inflow"
        return "neutral_flow"

    def _whale_state(self, *, whale_transfers: float) -> str:
        if whale_transfers > 100:
            return "high_whale_activity"
        if whale_transfers > 0:
            return "normal_whale_activity"
        return "quiet_whales"

    def _combine_bias(self, *, activity_state: str, flow_state: str, whale_state: str) -> str:
        score = 0

        if activity_state == "high_activity":
            score += 1
        elif activity_state == "low_activity":
            score -= 1

        if flow_state == "bullish_outflow":
            score += 2
        elif flow_state == "bearish_inflow":
            score -= 2

        if whale_state == "high_whale_activity":
            score += 0  # informational only, not directional by itself

        if score >= 2:
            return "bullish"
        if score <= -2:
            return "bearish"
        return "neutral"

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default