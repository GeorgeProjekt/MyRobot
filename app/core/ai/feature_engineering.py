from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


class FeatureEngineering:
    """
    Deterministic feature builder for price series.

    Output shape:
    {
        "return": [...],
        "returns": [...],
        "ma20": [...],
        "ma50": [...],
        "volatility": [...],
        "momentum": [...],
        "close": [...],
        "rows": int
    }

    Rules:
    - accepts pandas DataFrame with at least `close`
    - drops invalid rows safely
    - never returns raw pandas objects to upper layers
    """

    def build(self, df: Any) -> Dict[str, List[float] | int]:
        frame = self._normalize_df(df)
        if frame.empty:
            return {
                "return": [],
                "returns": [],
                "ma20": [],
                "ma50": [],
                "volatility": [],
                "momentum": [],
                "close": [],
                "rows": 0,
            }

        data = pd.DataFrame(index=frame.index.copy())
        data["close"] = frame["close"].astype(float)
        data["return"] = data["close"].pct_change()

        data["ma20"] = data["close"].rolling(20, min_periods=20).mean()
        data["ma50"] = data["close"].rolling(50, min_periods=50).mean()
        data["volatility"] = data["return"].rolling(20, min_periods=20).std()
        data["momentum"] = data["close"] - data["close"].shift(10)

        data = data.replace([float("inf"), float("-inf")], pd.NA).dropna()

        if data.empty:
            return {
                "return": [],
                "returns": [],
                "ma20": [],
                "ma50": [],
                "volatility": [],
                "momentum": [],
                "close": [],
                "rows": 0,
            }

        returns = [float(v) for v in data["return"].tolist()]
        ma20 = [float(v) for v in data["ma20"].tolist()]
        ma50 = [float(v) for v in data["ma50"].tolist()]
        volatility = [float(v) for v in data["volatility"].tolist()]
        momentum = [float(v) for v in data["momentum"].tolist()]
        close = [float(v) for v in data["close"].tolist()]

        return {
            "return": returns,
            "returns": returns,
            "ma20": ma20,
            "ma50": ma50,
            "volatility": volatility,
            "momentum": momentum,
            "close": close,
            "rows": len(close),
        }

    def _normalize_df(self, df: Any) -> pd.DataFrame:
        if isinstance(df, pd.DataFrame):
            frame = df.copy()
        else:
            try:
                frame = pd.DataFrame(df)
            except Exception:
                return pd.DataFrame(columns=["close"])

        if "close" not in frame.columns:
            return pd.DataFrame(columns=["close"])

        frame = frame[["close"]].copy()
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna()

        if frame.empty:
            return pd.DataFrame(columns=["close"])

        frame = frame[frame["close"] > 0]
        return frame

    def latest(self, df: Any) -> Dict[str, float | int | None]:
        built = self.build(df)
        if not built.get("rows"):
            return {
                "rows": 0,
                "return": None,
                "ma20": None,
                "ma50": None,
                "volatility": None,
                "momentum": None,
                "close": None,
            }
        return {
            "rows": int(built.get("rows", 0)),
            "return": built["returns"][-1] if built.get("returns") else None,
            "ma20": built["ma20"][-1] if built.get("ma20") else None,
            "ma50": built["ma50"][-1] if built.get("ma50") else None,
            "volatility": built["volatility"][-1] if built.get("volatility") else None,
            "momentum": built["momentum"][-1] if built.get("momentum") else None,
            "close": built["close"][-1] if built.get("close") else None,
        }
