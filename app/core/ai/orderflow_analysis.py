from __future__ import annotations

from typing import Any, Dict, List


class OrderflowAnalysis:
    """
    Deterministic orderflow analyzer.

    Purpose:
    - normalize bids / asks or orderbook-like inputs
    - derive bid/ask volume, spread and imbalance
    - return stable bias labels for downstream AI/risk modules
    """

    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        market_data = market_data if isinstance(market_data, dict) else {}

        orderbook = market_data.get("orderbook")
        if not isinstance(orderbook, dict):
            orderbook = market_data

        bids = self._normalize_side(orderbook.get("bids", []), reverse=True)
        asks = self._normalize_side(orderbook.get("asks", []), reverse=False)

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0

        bid_volume = sum(size for _price, size in bids[:10])
        ask_volume = sum(size for _price, size in asks[:10])

        spread = 0.0
        spread_pct = 0.0
        mid_price = 0.0

        if best_bid > 0.0 and best_ask > 0.0 and best_ask >= best_bid:
            spread = best_ask - best_bid
            mid_price = (best_bid + best_ask) / 2.0
            if mid_price > 0.0:
                spread_pct = spread / mid_price

        imbalance = 0.0
        total_volume = bid_volume + ask_volume
        if total_volume > 0.0:
            imbalance = (bid_volume - ask_volume) / total_volume

        bias = self._bias_from_imbalance(imbalance)

        liquidity = 0.0
        if total_volume > 0.0:
            liquidity = float(total_volume)

        return {
            "best_bid": float(best_bid),
            "best_ask": float(best_ask),
            "mid_price": float(mid_price),
            "spread": float(spread),
            "spread_pct": float(spread_pct),
            "bid_volume": float(bid_volume),
            "ask_volume": float(ask_volume),
            "imbalance": float(max(-1.0, min(1.0, imbalance))),
            "bias": bias,
            "liquidity": float(liquidity),
            "market_data_ok": bool(best_bid > 0.0 or best_ask > 0.0),
        }

    # ---------------------------------------------------------

    def _normalize_side(self, rows: Any, *, reverse: bool) -> List[tuple[float, float]]:
        out: List[tuple[float, float]] = []

        if not isinstance(rows, list):
            return out

        for row in rows:
            price = 0.0
            size = 0.0

            if isinstance(row, (list, tuple)) and len(row) >= 2:
                price = self._safe_float(row[0], 0.0)
                size = self._safe_float(row[1], 0.0)
            elif isinstance(row, dict):
                price = self._safe_float(row.get("price"), 0.0)
                size = self._safe_float(row.get("size") or row.get("amount"), 0.0)

            if price <= 0.0 or size <= 0.0:
                continue

            out.append((price, size))

        out.sort(key=lambda x: x[0], reverse=reverse)
        return out

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

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default