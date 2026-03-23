from __future__ import annotations
import time
from typing import Dict, Any, List


class FeatureStore:

    def __init__(self):
        self.data: List[Dict[str, Any]] = []

    def record(
        self,
        symbol: str,
        price: float,
        features: Dict[str, Any]
    ):

        self.data.append({
            "ts": int(time.time()),
            "symbol": symbol,
            "price": price,
            "features": features
        })

    def recent(self, symbol: str, limit: int = 100):

        rows = [x for x in self.data if x["symbol"] == symbol]

        return rows[-limit:]
