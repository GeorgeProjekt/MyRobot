from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_ts(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        try:
            value = float(value)
            if value > 10_000_000_000:
                value /= 1000.0
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None

    raw = str(value).strip()
    if not raw:
        return None

    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue

    try:
        num = float(raw)
        if num > 10_000_000_000:
            num /= 1000.0
        return datetime.fromtimestamp(num, tz=timezone.utc)
    except Exception:
        return None


def _latest_candle_ts(candles: List[Any]) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for item in list(candles or []):
        dt = None
        if isinstance(item, dict):
            for key in ("ts", "time", "timestamp", "date"):
                dt = _parse_ts(item.get(key))
                if dt is not None:
                    break
        elif isinstance(item, (list, tuple)) and item:
            dt = _parse_ts(item[0])
        if dt is not None and (latest is None or dt > latest):
            latest = dt
    return latest


def build_stale_data_snapshot(
    market_snapshot: Dict[str, Any],
    *,
    stale_after_sec: float,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    if now is None:
        now = datetime.now(timezone.utc)

    market = market_snapshot if isinstance(market_snapshot, dict) else {}
    ticker = market.get("ticker", {}) if isinstance(market.get("ticker"), dict) else {}
    chart = market.get("chart", {}) if isinstance(market.get("chart"), dict) else {}

    price_ts = (
        _parse_ts(ticker.get("ts"))
        or _parse_ts(ticker.get("timestamp"))
        or _parse_ts(chart.get("updated_at"))
        or _parse_ts(chart.get("ts"))
    )
    candle_ts = _latest_candle_ts(chart.get("candles", []) if isinstance(chart.get("candles"), list) else [])

    candidate_ts = candle_ts or price_ts
    age_sec = None
    if candidate_ts is not None:
        age_sec = max(0.0, (now - candidate_ts).total_seconds())

    price = _safe_float(
        ticker.get("last")
        or ticker.get("price")
        or ticker.get("lastPrice")
        or market.get("price")
        or 0.0,
        0.0,
    )

    stale = bool(candidate_ts is None or age_sec is None or age_sec > max(float(stale_after_sec), 5.0))
    severity = "critical" if stale else "ok"
    if not stale and age_sec is not None and age_sec > max(float(stale_after_sec) * 0.5, 5.0):
        severity = "warning"

    reasons: List[str] = []
    if candidate_ts is None:
        reasons.append("missing_timestamp")
    if price <= 0:
        reasons.append("invalid_price")
    if age_sec is not None and age_sec > float(stale_after_sec):
        reasons.append("stale_market_data")

    return {
        "ok": not stale and price > 0,
        "stale": stale,
        "severity": severity,
        "stale_after_sec": float(stale_after_sec),
        "age_sec": age_sec,
        "last_market_ts": candidate_ts.isoformat() if candidate_ts is not None else None,
        "last_price": price if price > 0 else None,
        "reasons": reasons,
    }
