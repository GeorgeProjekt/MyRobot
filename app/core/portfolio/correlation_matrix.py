from __future__ import annotations

from typing import Any, Dict

import pandas as pd


class CorrelationMatrix:
    def calculate(self, price_data: Dict[str, Any]) -> pd.DataFrame:
        returns = {}
        for pair, df in (price_data or {}).items():
            if not isinstance(df, pd.DataFrame) or df.empty or "close" not in df.columns:
                continue
            series = pd.to_numeric(df["close"], errors="coerce").dropna()
            if len(series) < 3:
                continue
            returns[str(pair).upper().strip()] = series.pct_change()
        if not returns:
            return pd.DataFrame()
        return pd.DataFrame(returns).corr()

    def to_serializable(self, corr: pd.DataFrame) -> Dict[str, Dict[str, float]]:
        if corr is None or getattr(corr, "empty", True):
            return {}
        out: Dict[str, Dict[str, float]] = {}
        for row_key in corr.index:
            out[str(row_key)] = {}
            for col_key in corr.columns:
                value = corr.loc[row_key, col_key]
                if pd.isna(value):
                    continue
                out[str(row_key)][str(col_key)] = float(value)
        return out
