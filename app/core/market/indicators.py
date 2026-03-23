from __future__ import annotations

from typing import Any, Dict

import pandas as pd


class Indicators:
    def ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = high_low.combine(high_close, max).combine(low_close, max)
        return tr.rolling(period).mean()

    def summary(self, df: pd.DataFrame) -> Dict[str, Any]:
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return {}
        if not {"open", "high", "low", "close"}.issubset(df.columns):
            return {}
        frame = df.copy()
        for col in ("open", "high", "low", "close"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        if frame.empty:
            return {}
        close = frame["close"]
        ema_fast = self.ema(close, 10)
        ema_slow = self.ema(close, 30)
        rsi14 = self.rsi(close, 14)
        atr14 = self.atr(frame, 14)
        ema_fast_last = float(ema_fast.iloc[-1]) if not ema_fast.empty else None
        ema_slow_last = float(ema_slow.iloc[-1]) if not ema_slow.empty else None
        trend = "neutral"
        if ema_fast_last is not None and ema_slow_last is not None:
            if ema_fast_last > ema_slow_last:
                trend = "bullish"
            elif ema_fast_last < ema_slow_last:
                trend = "bearish"
        return {
            "last_close": float(close.iloc[-1]),
            "ema_fast": ema_fast_last,
            "ema_slow": ema_slow_last,
            "rsi14": float(rsi14.iloc[-1]) if not rsi14.empty else None,
            "atr14": float(atr14.iloc[-1]) if not atr14.empty else None,
            "trend": trend,
            "rows": int(len(frame)),
        }
