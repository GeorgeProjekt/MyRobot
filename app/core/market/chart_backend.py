from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PAIR_CONFIG: Dict[str, Dict[str, str]] = {
    "BTC_EUR": {"coin": "bitcoin", "quote": "eur", "base": "BTC"},
    "BTC_CZK": {"coin": "bitcoin", "quote": "czk", "base": "BTC"},
    "ETH_EUR": {"coin": "ethereum", "quote": "eur", "base": "ETH"},
    "ETH_CZK": {"coin": "ethereum", "quote": "czk", "base": "ETH"},
    "ADA_CZK": {"coin": "cardano", "quote": "czk", "base": "ADA"},
}

COINMATE_TICKER_URL = "https://coinmate.io/api/ticker"
COINGECKO_SIMPLE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
COINGECKO_OHLC_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"

HTTP_TIMEOUT = 12
HTTP_HEADERS = {
    "User-Agent": "MyRobotDashboard/4.0",
    "Accept": "application/json",
}

SUCCESS_TTL = 300
EMPTY_TTL = 15

_cache_lock = threading.Lock()
_simple_cache: Dict[str, Any] = {"ts": 0.0, "data": {}}
_chart_cache: Dict[str, Any] = {}
_ticker_cache: Dict[str, Any] = {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _utc_ts() -> int:
    return int(time.time())


def _http_get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    full_url = url
    if params:
        full_url = f"{url}?{urlencode(params)}"
    req = Request(full_url, headers=HTTP_HEADERS)
    with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pair_cfg(pair: str) -> Dict[str, str]:
    pair = str(pair).upper().strip()
    if pair in PAIR_CONFIG:
        return PAIR_CONFIG[pair]
    if "_" in pair:
        base, quote = pair.split("_", 1)
        return {"coin": base.lower(), "quote": quote.lower(), "base": base.upper()}
    return {"coin": pair.lower(), "quote": "eur", "base": pair.upper()}


def fetch_simple_prices() -> Dict[str, Any]:
    now = time.time()
    with _cache_lock:
        if now - _safe_float(_simple_cache.get("ts"), 0.0) < 20:
            return _safe_dict(_simple_cache.get("data", {}))
    ids = ",".join(sorted({cfg["coin"] for cfg in PAIR_CONFIG.values()}))
    data = {}
    try:
        data = _safe_dict(_http_get_json(COINGECKO_SIMPLE_URL, {
            "ids": ids,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        }))
    except Exception:
        data = {}
    with _cache_lock:
        _simple_cache["ts"] = now
        _simple_cache["data"] = data
    return data


def fetch_coinmate_ticker(pair: str) -> Dict[str, Any]:
    pair = str(pair).upper().strip()
    now = time.time()
    with _cache_lock:
        entry = _safe_dict(_ticker_cache.get(pair, {}))
        if entry and (now - _safe_float(entry.get("ts"), 0.0) < 15):
            return entry
    out = {}
    try:
        raw = _http_get_json(COINMATE_TICKER_URL, {"currencyPair": pair.replace("_", "")})
        data = _safe_dict(raw.get("data", raw))
        last_price = _safe_float(data.get("last") or data.get("lastPrice"), 0.0)
        bid = _safe_float(data.get("bid"), 0.0)
        ask = _safe_float(data.get("ask"), 0.0)
        spread_abs = (ask - bid) if ask > 0 and bid > 0 and ask >= bid else 0.0
        spread_pct = ((spread_abs / ((ask + bid) / 2.0)) * 100.0) if spread_abs > 0 and (ask + bid) > 0 else None
        out = {
            "pair": pair,
            "price": last_price if last_price > 0 else None,
            "bid": bid if bid > 0 else None,
            "ask": ask if ask > 0 else None,
            "spread_pct": round(spread_pct, 6) if spread_pct is not None else None,
            "spread_abs": spread_abs if spread_abs > 0 else None,
            "source": "coinmate_ticker",
            "ts": _utc_ts(),
        }
    except Exception:
        out = {}
    with _cache_lock:
        _ticker_cache[pair] = {"ts": now, **out}
    return out


def _normalize_timeframe(timeframe: str) -> str:
    tf = str(timeframe or "1d").strip().lower()
    if tf in {"24h", "1h", "intraday", "day-1"}:
        return "24h"
    return "1d"


def _ohlc_supported_days(days: int) -> int:
    for supported in (1, 7, 14, 30, 90):
        if days <= supported:
            return supported
    return 90


def _normalize_candle_row(row: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    ts = _safe_int(row.get("time") or row.get("timestamp"), 0)
    if ts > 10_000_000_000:
        ts //= 1000
    open_ = _safe_float(row.get("open"), 0.0)
    high = _safe_float(row.get("high"), 0.0)
    low = _safe_float(row.get("low"), 0.0)
    close = _safe_float(row.get("close"), 0.0)
    volume = max(_safe_float(row.get("volume"), 0.0), 0.0)
    if not ts or min(open_, high, low, close) <= 0:
        return None
    return {
        "time": ts,
        "open": open_,
        "high": max(high, open_, close),
        "low": min(low, open_, close),
        "close": close,
        "volume": volume,
        "value": close,
    }


def _normalize_ohlc_rows(rows: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows or []:
        if isinstance(row, list):
            if len(row) >= 5:
                ts = _safe_int(row[0], 0)
                if ts > 10_000_000_000:
                    ts //= 1000
                candle = {
                    "time": ts,
                    "open": _safe_float(row[1], 0.0),
                    "high": _safe_float(row[2], 0.0),
                    "low": _safe_float(row[3], 0.0),
                    "close": _safe_float(row[4], 0.0),
                    "volume": _safe_float(row[5], 0.0) if len(row) > 5 else 0.0,
                }
            else:
                candle = None
        else:
            candle = _normalize_candle_row(row)
        if candle and candle["time"] not in seen and min(candle["open"], candle["high"], candle["low"], candle["close"]) > 0:
            candle["high"] = max(candle["high"], candle["open"], candle["close"])
            candle["low"] = min(candle["low"], candle["open"], candle["close"])
            candle["value"] = candle["close"]
            seen.add(candle["time"])
            out.append(candle)
    out.sort(key=lambda c: c["time"])
    return out


def _aggregate_market_chart(prices: List[List[Any]], volumes: List[List[Any]], bucket_sec: int) -> List[Dict[str, Any]]:
    volume_map: Dict[int, float] = {}
    for row in volumes or []:
        if not isinstance(row, list) or len(row) < 2:
            continue
        ts = _safe_int(row[0], 0)
        if ts > 10_000_000_000:
            ts //= 1000
        if ts <= 0:
            continue
        bucket = ts - (ts % bucket_sec)
        volume_map[bucket] = volume_map.get(bucket, 0.0) + max(_safe_float(row[1], 0.0), 0.0)

    buckets: Dict[int, Dict[str, Any]] = {}
    for row in prices or []:
        if not isinstance(row, list) or len(row) < 2:
            continue
        ts = _safe_int(row[0], 0)
        if ts > 10_000_000_000:
            ts //= 1000
        price = _safe_float(row[1], 0.0)
        if ts <= 0 or price <= 0:
            continue
        bucket = ts - (ts % bucket_sec)
        current = buckets.get(bucket)
        if current is None:
            current = {
                "time": bucket,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume_map.get(bucket, 0.0),
            }
            buckets[bucket] = current
        else:
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price
    return _normalize_ohlc_rows(list(buckets.values()))


def _slice_to_days(candles: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    if not candles:
        return []
    if days <= 1:
        # Keep a meaningful intraday window rather than one daily candle.
        return candles[-96:] if len(candles) > 96 else candles
    return candles[-days:] if len(candles) > days else candles


def _ema_series(candles: List[Dict[str, Any]], period: int) -> List[Dict[str, Any]]:
    if not candles or period <= 1:
        return []
    multiplier = 2.0 / (period + 1.0)
    ema: Optional[float] = None
    out: List[Dict[str, Any]] = []
    for candle in candles:
        close = _safe_float(candle.get("close"), 0.0)
        if close <= 0:
            continue
        ema = close if ema is None else ((close - ema) * multiplier) + ema
        out.append({"time": candle["time"], "value": round(float(ema), 8)})
    return out


def _build_overlay(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not candles:
        return {"indicators": {}, "available": False}
    indicators: Dict[str, Any] = {}
    if len(candles) >= 5:
        ema20 = _ema_series(candles, 20)
        if ema20:
            indicators["ema20"] = ema20
    if len(candles) >= 10:
        ema50 = _ema_series(candles, 50)
        if ema50:
            indicators["ema50"] = ema50
    return {
        "indicators": indicators,
        "available": bool(indicators),
    }


def _cache_chart_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    with _cache_lock:
        _chart_cache[payload["cache_key"]] = payload
    return payload


def _find_pair_cache_fallback(pair: str, preferred_timeframe: str) -> Optional[Dict[str, Any]]:
    with _cache_lock:
        candidates = []
        for key, value in _chart_cache.items():
            entry = _safe_dict(value)
            if entry.get("pair") != pair or not entry.get("candles"):
                continue
            score = 0
            if entry.get("timeframe") == preferred_timeframe:
                score += 10
            score += min(len(entry.get("candles") or []), 500)
            score -= int(max(0.0, time.time() - _safe_float(entry.get("cached_at"), 0.0)))
            candidates.append((score, key, entry))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return dict(candidates[0][2])


def _build_payload(
    *,
    pair: str,
    timeframe: str,
    days: int,
    candles: List[Dict[str, Any]],
    source: Optional[str],
    source_state: str,
    cache_key: str,
    fetch_error: Optional[str],
    stale: bool = False,
    degraded: bool = False,
    requested_days: Optional[int] = None,
) -> Dict[str, Any]:
    candles = _normalize_ohlc_rows(candles)
    overlay = _build_overlay(candles)
    payload = {
        "pair": pair,
        "timeframe": timeframe,
        "days": days,
        "requested_days": requested_days or days,
        "candles": candles,
        "series": candles,
        "overlay": overlay,
        "source": source,
        "source_state": source_state,
        "current_price": candles[-1]["close"] if candles else None,
        "cached_at": time.time(),
        "cache_key": cache_key,
        "ts": _utc_ts(),
        "last_error": fetch_error,
        "stale": stale,
        "degraded": degraded,
        "meta": {
            "available": bool(candles),
            "stale": stale,
            "degraded": degraded,
            "source_state": source_state,
            "requested_days": requested_days or days,
            "available_points": len(candles),
        },
    }
    return payload


def _fetch_intraday_chart(cfg: Dict[str, str], days: int) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    errors: List[str] = []

    try:
        raw = _http_get_json(COINGECKO_MARKET_CHART_URL.format(coin_id=cfg["coin"]), {
            "vs_currency": cfg["quote"],
            "days": 1,
        })
        raw = _safe_dict(raw)
        candles = _aggregate_market_chart(raw.get("prices", []) or [], raw.get("total_volumes", []) or [], 15 * 60)
        candles = _slice_to_days(candles, days)
        if candles:
            return candles, "coingecko_market_chart_24h", None
    except Exception as exc:
        errors.append(f"market_chart_24h:{type(exc).__name__}: {exc}")

    try:
        raw = _http_get_json(COINGECKO_OHLC_URL.format(coin_id=cfg["coin"]), {
            "vs_currency": cfg["quote"],
            "days": 1,
        })
        candles = _normalize_ohlc_rows(raw if isinstance(raw, list) else [])
        candles = _slice_to_days(candles, days)
        if candles:
            return candles, "coingecko_ohlc_24h", None
    except Exception as exc:
        errors.append(f"ohlc_24h:{type(exc).__name__}: {exc}")

    return [], None, " | ".join(errors) if errors else None


def _fetch_daily_chart(cfg: Dict[str, str], days: int) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str], bool]:
    errors: List[str] = []

    try:
        raw = _http_get_json(COINGECKO_MARKET_CHART_URL.format(coin_id=cfg["coin"]), {
            "vs_currency": cfg["quote"],
            "days": days,
        })
        raw = _safe_dict(raw)
        candles = _aggregate_market_chart(raw.get("prices", []) or [], raw.get("total_volumes", []) or [], 24 * 60 * 60)
        candles = _slice_to_days(candles, days)
        if candles:
            return candles, "coingecko_market_chart_daily", None, False
    except Exception as exc:
        errors.append(f"market_chart_daily:{type(exc).__name__}: {exc}")

    try:
        supported = _ohlc_supported_days(days)
        raw = _http_get_json(COINGECKO_OHLC_URL.format(coin_id=cfg["coin"]), {
            "vs_currency": cfg["quote"],
            "days": supported,
        })
        candles = _normalize_ohlc_rows(raw if isinstance(raw, list) else [])
        candles = _slice_to_days(candles, days)
        if candles:
            degraded = supported < days
            return candles, "coingecko_ohlc", None, degraded
    except Exception as exc:
        errors.append(f"ohlc_daily:{type(exc).__name__}: {exc}")

    if days > 90:
        try:
            raw = _http_get_json(COINGECKO_MARKET_CHART_URL.format(coin_id=cfg["coin"]), {
                "vs_currency": cfg["quote"],
                "days": 90,
            })
            raw = _safe_dict(raw)
            candles = _aggregate_market_chart(raw.get("prices", []) or [], raw.get("total_volumes", []) or [], 24 * 60 * 60)
            candles = _slice_to_days(candles, 90)
            if candles:
                return candles, "coingecko_market_chart_daily_90d_fallback", None, True
        except Exception as exc:
            errors.append(f"market_chart_90d_fallback:{type(exc).__name__}: {exc}")

    return [], None, " | ".join(errors) if errors else None, False


def fetch_chart(pair: str, *, timeframe: str, days: int) -> Dict[str, Any]:
    pair = str(pair).upper().strip()
    timeframe = _normalize_timeframe(timeframe)
    days = max(1, min(int(days or 1), 365))
    cache_key = f"{pair}:{timeframe}:{days}"
    now = time.time()

    with _cache_lock:
        entry = _safe_dict(_chart_cache.get(cache_key, {}))
        if entry:
            ttl = SUCCESS_TTL if entry.get("candles") else EMPTY_TTL
            age = now - _safe_float(entry.get("cached_at"), 0.0)
            if age < ttl:
                return dict(entry)

    cfg = pair_cfg(pair)
    candles: List[Dict[str, Any]] = []
    source: Optional[str] = None
    fetch_error: Optional[str] = None
    degraded = False

    if timeframe == "24h":
        candles, source, fetch_error = _fetch_intraday_chart(cfg, days)
    else:
        candles, source, fetch_error, degraded = _fetch_daily_chart(cfg, days)

    if candles:
        payload = _build_payload(
            pair=pair,
            timeframe=timeframe,
            days=days,
            candles=candles,
            source=source,
            source_state="reference_live" if not degraded else "reference_degraded",
            cache_key=cache_key,
            fetch_error=fetch_error,
            degraded=degraded,
            requested_days=days,
        )
        return _cache_chart_payload(payload)

    previous_entry = _safe_dict(_chart_cache.get(cache_key, {}))
    if previous_entry.get("candles"):
        payload = _build_payload(
            pair=pair,
            timeframe=timeframe,
            days=days,
            candles=previous_entry.get("candles", []) or [],
            source=previous_entry.get("source"),
            source_state="cached_stale",
            cache_key=cache_key,
            fetch_error=fetch_error,
            stale=True,
            degraded=True,
            requested_days=days,
        )
        return _cache_chart_payload(payload)

    fallback_entry = _find_pair_cache_fallback(pair, timeframe)
    if fallback_entry and fallback_entry.get("candles"):
        fallback_days = _safe_int(fallback_entry.get("days"), days)
        payload = _build_payload(
            pair=pair,
            timeframe=timeframe,
            days=fallback_days,
            candles=fallback_entry.get("candles", []) or [],
            source=fallback_entry.get("source"),
            source_state="cached_pair_fallback",
            cache_key=cache_key,
            fetch_error=fetch_error,
            stale=True,
            degraded=True,
            requested_days=days,
        )
        return _cache_chart_payload(payload)

    if timeframe != "24h":
        intraday_candles, intraday_source, intraday_error = _fetch_intraday_chart(cfg, max(1, min(days, 7)))
        if intraday_candles:
            combined_error = " | ".join([part for part in [fetch_error, intraday_error] if part]) or None
            payload = _build_payload(
                pair=pair,
                timeframe=timeframe,
                days=max(1, min(days, 7)),
                candles=intraday_candles,
                source=f"{intraday_source or 'intraday'}_fullscreen_fallback",
                source_state="intraday_fallback",
                cache_key=cache_key,
                fetch_error=combined_error,
                stale=False,
                degraded=True,
                requested_days=days,
            )
            return _cache_chart_payload(payload)

    payload = _build_payload(
        pair=pair,
        timeframe=timeframe,
        days=days,
        candles=[],
        source=source,
        source_state="unavailable",
        cache_key=cache_key,
        fetch_error=fetch_error,
        requested_days=days,
    )
    return _cache_chart_payload(payload)
