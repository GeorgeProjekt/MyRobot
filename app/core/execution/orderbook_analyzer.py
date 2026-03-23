from __future__ import annotations

from typing import Any, Dict, List, Tuple


class OrderBookAnalyzer:
    """
    Deterministic order book analyzer.

    Responsibilities:
    - normalize bids / asks safely
    - compute top-N bid/ask volume
    - compute spread and mid price
    - compute imbalance in stable range [-1, 1]
    """

    def analyse(self, orderbook: Dict[str, Any], depth: int = 10) -> Dict[str, float]:
        book = orderbook if isinstance(orderbook, dict) else {}
        top_n = max(int(depth), 1)

        bids = self._normalize_side(book.get("bids", []), reverse=True)[:top_n]
        asks = self._normalize_side(book.get("asks", []), reverse=False)[:top_n]

        bid_volume = sum(size for _price, size in bids)
        ask_volume = sum(size for _price, size in asks)

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0

        mid_price = 0.0
        spread = 0.0
        spread_pct = 0.0

        if best_bid > 0.0 and best_ask > 0.0 and best_ask >= best_bid:
            mid_price = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid
            if mid_price > 0.0:
                spread_pct = spread / mid_price

        imbalance = 0.0
        denom = bid_volume + ask_volume
        if denom > 0.0:
            imbalance = (bid_volume - ask_volume) / denom

        return {
            "best_bid": float(best_bid),
            "best_ask": float(best_ask),
            "mid_price": float(mid_price),
            "spread": float(spread),
            "spread_pct": float(spread_pct),
            "bid_volume": float(bid_volume),
            "ask_volume": float(ask_volume),
            "imbalance": float(max(-1.0, min(1.0, imbalance))),
            "depth_used": float(top_n),
        }

    # ---------------------------------------------------------

    def _normalize_side(self, rows: Any, *, reverse: bool) -> List[Tuple[float, float]]:
        out: List[Tuple[float, float]] = []

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

        out.sort(key=lambda item: item[0], reverse=reverse)
        return out

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default