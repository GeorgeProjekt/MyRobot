from __future__ import annotations

from typing import Any, Dict

import pandas as pd


class VolatilityForecast:
    def _normalize_df(self, payload: Any) -> pd.DataFrame:
        if isinstance(payload, pd.DataFrame):
            df = payload.copy()
        elif isinstance(payload, dict):
            if isinstance(payload.get("ohlcv"), pd.DataFrame):
                df = payload["ohlcv"].copy()
            else:
                df = pd.DataFrame(payload.get("ohlcv") or payload.get("candles") or payload.get("history") or [])
        else:
            df = pd.DataFrame(payload or [])
        if df.empty or "close" not in df.columns:
            return pd.DataFrame(columns=["close"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"])
        return df

    def predict(self, market_data: Any) -> Dict[str, Any]:
        df = self._normalize_df(market_data)
        if df.empty or len(df) < 30:
            return {"value": None, "regime": "unknown", "rows": len(df)}
        returns = df["close"].pct_change()
        vol = returns.rolling(30, min_periods=30).std().dropna()
        if vol.empty:
            return {"value": None, "regime": "unknown", "rows": len(df)}
        current = float(vol.iloc[-1])
        return {"value": current, "regime": self._classify_regime(current), "rows": len(df)}

    def _classify_regime(self, vol: float) -> str:
        if vol < 0.01:
            return "low"
        if vol < 0.03:
            return "medium"
        return "high"
