from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from app.core.market.ohlcv_provider import OHLCVProvider


class MarketData:
    """Runtime-safe OHLCV loader aligned with the project market providers."""

    def __init__(self, timeframe: str = "1d"):
        self.timeframe = timeframe
        self.provider = OHLCVProvider()

    def set_timeframe(self, timeframe: str) -> None:
        self.timeframe = str(timeframe or "1d")

    def fetch_ohlcv_df(self, symbol: str, limit: int = 300, market_data: Optional[Any] = None) -> pd.DataFrame:
        return self.provider.get_ohlcv_df(
            pair=str(symbol).upper().strip(),
            timeframe=self.timeframe,
            limit=limit,
            market_data=market_data,
        )
