from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


class OHLCVProvider:
    COINGECKO_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    HTTP_TIMEOUT = 12
    CACHE_TTL = 60

    PAIR_CONFIG: Dict[str, Dict[str, str]] = {
        "BTC_EUR": {"coin": "bitcoin", "quote": "eur"},
        "BTC_CZK": {"coin": "bitcoin", "quote": "czk"},
        "ETH_EUR": {"coin": "ethereum", "quote": "eur"},
        "ETH_CZK": {"coin": "ethereum", "quote": "czk"},
        "ADA_CZK": {"coin": "cardano", "quote": "czk"},
    }

    def __init__(self) -> None:
        self._cache: Dict[str, tuple[float, pd.DataFrame]] = {}

    def get_ohlcv_df(
        self,
        pair: str,
        timeframe: str = "1d",
        limit: int = 120,
        market_data: Optional[Any] = None,
    ) -> pd.DataFrame:
        normalized_timeframe = self._normalize_timeframe(timeframe)

        df = self.from_payload(market_data)
        if not df.empty:
            return df.tail(max(1, int(limit))).reset_index(drop=True)

        pair = str(pair or "").upper().strip()
        cache_key = f"{pair}:{normalized_timeframe}:{int(limit)}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < self.CACHE_TTL:
            return cached[1].copy()

        df = self._fetch_pair_history(pair=pair, timeframe=normalized_timeframe, limit=limit)
        self._cache[cache_key] = (now, df.copy())
        return df

    def from_payload(self, payload: Any) -> pd.DataFrame:
        if payload is None:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

        rows = self._extract_rows_from_payload(payload)

        if isinstance(rows, pd.DataFrame):
            df = rows.copy()
        else:
            try:
                df = pd.DataFrame(rows or [])
            except Exception:
                return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

        if df.empty:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

        rename_map = {}
        for col in df.columns:
            low = str(col).lower()
            if low in {"timestamp", "ts"}:
                rename_map[col] = "time"
            elif low == "o":
                rename_map[col] = "open"
            elif low == "h":
                rename_map[col] = "high"
            elif low == "l":
                rename_map[col] = "low"
            elif low in {"c", "value"}:
                rename_map[col] = "close"
            elif low in {"v", "vol"}:
                rename_map[col] = "volume"

        if rename_map:
            df = df.rename(columns=rename_map)

        expected = ["time", "open", "high", "low", "close", "volume"]
        for col in expected:
            if col not in df.columns:
                if col == "volume":
                    df[col] = 0.0
                else:
                    return pd.DataFrame(columns=expected)

        for col in ("open", "high", "low", "close", "volume", "time"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["time", "open", "high", "low", "close"])
        df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]

        return df[expected].sort_values("time").reset_index(drop=True)

    def _extract_rows_from_payload(self, payload: Any) -> Any:
        if payload is None:
            return []

        if isinstance(payload, pd.DataFrame):
            return payload

        if not isinstance(payload, dict):
            return payload

        direct_rows = payload.get("ohlcv") or payload.get("candles") or payload.get("history")
        if direct_rows is not None:
            return direct_rows

        for nested_key in ("market", "data", "snapshot", "payload"):
            nested = payload.get(nested_key)
            if isinstance(nested, dict):
                nested_rows = nested.get("ohlcv") or nested.get("candles") or nested.get("history")
                if nested_rows is not None:
                    return nested_rows

        return []

    def _normalize_timeframe(self, timeframe: str) -> str:
        tf = str(timeframe or "1d").strip().lower()
        aliases = {
            "daily": "1d",
            "day": "1d",
            "d": "1d",
            "hour": "1h",
            "hourly": "1h",
            "h": "1h",
            "24h": "1h",
        }
        tf = aliases.get(tf, tf)
        if tf in {"1d", "1h", "4h", "6h", "12h"}:
            return tf
        return "1d"

    def _timeframe_seconds(self, timeframe: str) -> int:
        mapping = {
            "1h": 3600,
            "4h": 14400,
            "6h": 21600,
            "12h": 43200,
            "1d": 86400,
        }
        return mapping.get(timeframe, 86400)

    def _recommended_days(self, timeframe: str, limit: int) -> int:
        step_seconds = self._timeframe_seconds(timeframe)
        horizon_seconds = max(1, int(limit)) * step_seconds
        horizon_days = int(horizon_seconds / 86400) + 3
        return max(1, min(365, horizon_days))

    def _fetch_pair_history(self, pair: str, timeframe: str = "1d", limit: int = 120) -> pd.DataFrame:
        cfg = self.PAIR_CONFIG.get(pair)
        if not cfg:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

        days = self._recommended_days(timeframe, limit)

        req = Request(
            f"{self.COINGECKO_MARKET_CHART_URL.format(coin_id=cfg['coin'])}?{urlencode({'vs_currency': cfg['quote'], 'days': days})}",
            headers={"User-Agent": "MyRobot/1.0", "Accept": "application/json"},
        )

        try:
            with urlopen(req, timeout=self.HTTP_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

        prices = payload.get("prices") if isinstance(payload, dict) else []
        volumes = payload.get("total_volumes") if isinstance(payload, dict) else []

        if timeframe == "1d":
            return self._aggregate_market_chart_rows(
                prices=prices,
                volumes=volumes,
                bucket_seconds=86400,
                limit=limit,
            )

        bucket_seconds = self._timeframe_seconds(timeframe)
        return self._aggregate_market_chart_rows(
            prices=prices,
            volumes=volumes,
            bucket_seconds=bucket_seconds,
            limit=limit,
        )

    def _aggregate_market_chart_rows(
        self,
        *,
        prices: Any,
        volumes: Any,
        bucket_seconds: int,
        limit: int,
    ) -> pd.DataFrame:
        expected = ["time", "open", "high", "low", "close", "volume"]

        vol_map: Dict[int, float] = {}
        for row in volumes or []:
            if not isinstance(row, list) or len(row) < 2:
                continue
            try:
                ts = int(float(row[0]) / 1000)
                bucket = ts - (ts % bucket_seconds)
                vol_map[bucket] = float(row[1] or 0.0)
            except Exception:
                continue

        buckets: Dict[int, Dict[str, float]] = {}
        for row in prices or []:
            if not isinstance(row, list) or len(row) < 2:
                continue
            try:
                ts = int(float(row[0]) / 1000)
                price = float(row[1] or 0.0)
            except Exception:
                continue

            if ts <= 0 or price <= 0:
                continue

            bucket = ts - (ts % bucket_seconds)
            candle = buckets.get(bucket)
            if candle is None:
                buckets[bucket] = {
                    "time": float(bucket),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": vol_map.get(bucket, 0.0),
                }
            else:
                candle["high"] = max(candle["high"], price)
                candle["low"] = min(candle["low"], price)
                candle["close"] = price

        rows = [buckets[key] for key in sorted(buckets.keys())][-max(1, int(limit)):]
        if not rows:
            return pd.DataFrame(columns=expected)

        return self.from_payload(rows)