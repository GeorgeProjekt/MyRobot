from __future__ import annotations

from typing import Any, Dict, Optional


class LiquidityFilter:
    """
    Pair-isolated deterministic liquidity filter.

    Responsibilities:
    - accept normalized signal candidate
    - evaluate spread / orderbook liquidity / market depth inputs
    - block trades in illiquid conditions
    - return stable allow/reject payload
    """

    def __init__(
        self,
        pair: str,
        *,
        max_spread_pct: float = 0.01,
        min_liquidity: float = 0.0,
        min_bid_ask_volume: float = 0.0,
    ) -> None:
        self.pair = str(pair).upper().strip()
        self.max_spread_pct = max(float(max_spread_pct), 0.0)
        self.min_liquidity = max(float(min_liquidity), 0.0)
        self.min_bid_ask_volume = max(float(min_bid_ask_volume), 0.0)

    # ---------------------------------------------------------

    def check(self, signal: Dict[str, Any], market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        candidate = signal if isinstance(signal, dict) else {}
        context = market_context if isinstance(market_context, dict) else {}

        pair = str(candidate.get("pair") or self.pair).upper().strip()
        if pair != self.pair:
            return self._reject("pair_mismatch")

        spread_pct = self._extract_spread_pct(candidate, context)
        liquidity = self._extract_liquidity(candidate, context)
        bid_volume = self._extract_bid_volume(candidate, context)
        ask_volume = self._extract_ask_volume(candidate, context)

        if self.max_spread_pct > 0.0 and spread_pct > self.max_spread_pct:
            return self._reject(
                "spread_too_wide",
                spread_pct=spread_pct,
                liquidity=liquidity,
                bid_volume=bid_volume,
                ask_volume=ask_volume,
            )

        if self.min_liquidity > 0.0 and liquidity < self.min_liquidity:
            return self._reject(
                "liquidity_too_low",
                spread_pct=spread_pct,
                liquidity=liquidity,
                bid_volume=bid_volume,
                ask_volume=ask_volume,
            )

        if self.min_bid_ask_volume > 0.0:
            if bid_volume < self.min_bid_ask_volume or ask_volume < self.min_bid_ask_volume:
                return self._reject(
                    "bid_ask_volume_too_low",
                    spread_pct=spread_pct,
                    liquidity=liquidity,
                    bid_volume=bid_volume,
                    ask_volume=ask_volume,
                )

        return {
            "ok": True,
            "allowed": True,
            "pair": self.pair,
            "reason": "ok",
            "spread_pct": float(spread_pct),
            "liquidity": float(liquidity),
            "bid_volume": float(bid_volume),
            "ask_volume": float(ask_volume),
        }

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _extract_spread_pct(self, signal: Dict[str, Any], context: Dict[str, Any]) -> float:
        for source in (signal, context, context.get("orderflow") if isinstance(context.get("orderflow"), dict) else {}):
            if not isinstance(source, dict):
                continue
            value = self._safe_float(source.get("spread_pct"), None)
            if value is not None and value >= 0.0:
                return float(value)

        bid = self._safe_float(signal.get("bid"), None)
        ask = self._safe_float(signal.get("ask"), None)
        if bid is not None and ask is not None and bid > 0.0 and ask >= bid:
            mid = (bid + ask) / 2.0
            if mid > 0.0:
                return (ask - bid) / mid

        return 0.0

    def _extract_liquidity(self, signal: Dict[str, Any], context: Dict[str, Any]) -> float:
        for source in (signal, context, context.get("orderflow") if isinstance(context.get("orderflow"), dict) else {}):
            if not isinstance(source, dict):
                continue
            value = self._safe_float(source.get("liquidity"), None)
            if value is not None and value >= 0.0:
                return float(value)

        bid_volume = self._extract_bid_volume(signal, context)
        ask_volume = self._extract_ask_volume(signal, context)
        return float(bid_volume + ask_volume)

    def _extract_bid_volume(self, signal: Dict[str, Any], context: Dict[str, Any]) -> float:
        for source in (signal, context, context.get("orderflow") if isinstance(context.get("orderflow"), dict) else {}):
            if not isinstance(source, dict):
                continue
            value = self._safe_float(source.get("bid_volume"), None)
            if value is not None and value >= 0.0:
                return float(value)
        return 0.0

    def _extract_ask_volume(self, signal: Dict[str, Any], context: Dict[str, Any]) -> float:
        for source in (signal, context, context.get("orderflow") if isinstance(context.get("orderflow"), dict) else {}):
            if not isinstance(source, dict):
                continue
            value = self._safe_float(source.get("ask_volume"), None)
            if value is not None and value >= 0.0:
                return float(value)
        return 0.0

    def _reject(
        self,
        reason: str,
        *,
        spread_pct: float = 0.0,
        liquidity: float = 0.0,
        bid_volume: float = 0.0,
        ask_volume: float = 0.0,
    ) -> Dict[str, Any]:
        return {
            "ok": False,
            "allowed": False,
            "pair": self.pair,
            "reason": reason,
            "spread_pct": float(spread_pct),
            "liquidity": float(liquidity),
            "bid_volume": float(bid_volume),
            "ask_volume": float(ask_volume),
        }

    def _safe_float(self, value: Any, default: Any = 0.0) -> Any:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default