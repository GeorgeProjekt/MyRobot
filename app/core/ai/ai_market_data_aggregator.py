from __future__ import annotations

from typing import Any, Dict, Iterable, List


class AIMarketDataAggregator:
    """
    Deterministic market data aggregator.

    Purpose:
    - normalize multi-source payloads into one safe structure
    - keep only truthful numeric values
    - avoid random fallback values
    """

    def aggregate(self, *sources: Any) -> Dict[str, Any]:
        merged: Dict[str, Any] = {
            "price": 0.0,
            "close": 0.0,
            "last": 0.0,
            "bid": 0.0,
            "ask": 0.0,
            "volume": 0.0,
            "atr": 0.0,
            "volatility": 0.0,
            "trend": "",
            "regime": "",
            "closes": [],
            "candles": [],
            "market_data_ok": False,
            "source_count": 0,
        }

        used = 0

        for source in sources:
            payload = self._safe_dict(source)
            if not payload:
                continue

            used += 1

            merged["price"] = self._pick_positive(merged["price"], payload.get("price"))
            merged["close"] = self._pick_positive(merged["close"], payload.get("close"))
            merged["last"] = self._pick_positive(merged["last"], payload.get("last"))
            merged["bid"] = self._pick_positive(merged["bid"], payload.get("bid"))
            merged["ask"] = self._pick_positive(merged["ask"], payload.get("ask"))
            merged["volume"] = self._pick_positive(merged["volume"], payload.get("volume"))
            merged["atr"] = self._pick_positive(merged["atr"], payload.get("atr"))
            merged["volatility"] = self._pick_positive(merged["volatility"], payload.get("volatility"))

            if not merged["trend"]:
                merged["trend"] = str(payload.get("trend") or "").lower().strip()

            if not merged["regime"]:
                merged["regime"] = str(payload.get("regime") or "").lower().strip()

            closes = self._extract_closes(payload)
            if closes:
                merged["closes"] = closes

            candles = self._extract_candles(payload)
            if candles:
                merged["candles"] = candles

        if merged["price"] <= 0.0:
            merged["price"] = self._pick_positive(0.0, merged["close"])
        if merged["price"] <= 0.0:
            merged["price"] = self._pick_positive(0.0, merged["last"])
        if merged["close"] <= 0.0:
            merged["close"] = self._pick_positive(0.0, merged["price"])
        if merged["last"] <= 0.0:
            merged["last"] = self._pick_positive(0.0, merged["price"])

        merged["market_data_ok"] = bool(merged["price"] > 0.0)
        merged["source_count"] = used

        return merged

    # ---------------------------------------------------------

    def _extract_closes(self, payload: Dict[str, Any]) -> List[float]:
        direct = payload.get("closes")
        if isinstance(direct, list):
            out = [float(v) for v in direct if self._is_positive(v)]
            if out:
                return out

        history = payload.get("history")
        if isinstance(history, list):
            out: List[float] = []
            for row in history:
                if isinstance(row, dict):
                    value = row.get("close", row.get("price"))
                    if self._is_positive(value):
                        out.append(float(value))
            if out:
                return out

        candles = payload.get("candles")
        if isinstance(candles, list):
            out = []
            for row in candles:
                if isinstance(row, dict):
                    value = row.get("close")
                    if self._is_positive(value):
                        out.append(float(value))
            if out:
                return out

        return []

    def _extract_candles(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        candles = payload.get("candles")
        if not isinstance(candles, list):
            return []

        out: List[Dict[str, Any]] = []
        for row in candles:
            if not isinstance(row, dict):
                continue

            close = self._safe_float(row.get("close"), 0.0)
            if close <= 0.0:
                continue

            candle = {
                "time": self._safe_int(row.get("time"), 0),
                "open": self._safe_float(row.get("open"), close),
                "high": self._safe_float(row.get("high"), close),
                "low": self._safe_float(row.get("low"), close),
                "close": close,
            }
            out.append(candle)

        return out

    def _pick_positive(self, current: Any, candidate: Any) -> float:
        current_f = self._safe_float(current, 0.0)
        candidate_f = self._safe_float(candidate, 0.0)

        if candidate_f > 0.0:
            return candidate_f
        return current_f

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def _is_positive(self, value: Any) -> bool:
        try:
            return float(value) > 0.0
        except Exception:
            return False