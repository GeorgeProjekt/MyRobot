from __future__ import annotations

from typing import Any, Dict, List, Optional


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _ema(values: List[float], period: int) -> List[Optional[float]]:
    if not values or period <= 0:
        return []
    out: List[Optional[float]] = []
    mult = 2.0 / (period + 1.0)
    acc: Optional[float] = None
    for value in values:
        if acc is None:
            acc = value
        else:
            acc = (value - acc) * mult + acc
        out.append(acc)
    return out


def _atr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    if len(candles) < 2:
        return None
    trs: List[float] = []
    prev_close: Optional[float] = None
    for candle in candles:
        high = _safe_float(candle.get("high"))
        low = _safe_float(candle.get("low"))
        close = _safe_float(candle.get("close"))
        if high <= 0 or low <= 0 or close <= 0:
            continue
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = close
    if not trs:
        return None
    tail = trs[-max(1, period):]
    return sum(tail) / float(len(tail))


def build_trend_following_plan(pair_name: str, candles: List[Dict[str, Any]], *, ema_fast: int = 20, ema_slow: int = 50, atr_period: int = 14, rr: float = 2.0) -> Dict[str, Any]:
    closes = [_safe_float(c.get("close")) for c in candles if _safe_float(c.get("close")) > 0]
    if len(closes) < max(ema_fast, ema_slow, atr_period) + 5:
        return {
            "pair": pair_name,
            "strategy": "trend_following_b31",
            "ready": False,
            "signal": "HOLD",
            "side": None,
            "regime": "insufficient_data",
            "reason": "not_enough_candles",
        }

    ema_fast_series = _ema(closes, ema_fast)
    ema_slow_series = _ema(closes, ema_slow)
    last_close = closes[-1]
    fast = float(ema_fast_series[-1] or last_close)
    slow = float(ema_slow_series[-1] or last_close)
    atr = _atr(candles, period=atr_period)
    atr = float(atr or max(last_close * 0.01, 1e-8))

    slope_fast = fast - float(ema_fast_series[-2] or fast)
    slope_slow = slow - float(ema_slow_series[-2] or slow)
    spread = fast - slow
    spread_pct = (spread / slow * 100.0) if slow else 0.0

    if fast > slow and slope_fast >= 0 and slope_slow >= 0:
        regime = "bullish_trend"
        signal = "BUY"
        side = "LONG"
    elif fast < slow and slope_fast <= 0 and slope_slow <= 0:
        regime = "bearish_trend"
        signal = "SELL"
        side = "SHORT"
    else:
        regime = "transition"
        signal = "HOLD"
        side = None

    stop_mult = 1.5
    tp_mult = max(rr * stop_mult, stop_mult + 0.5)
    trailing_mult = 1.25

    entry = last_close
    stop_loss = None
    take_profit = None
    trailing_distance = None
    if side == "LONG":
        stop_loss = max(entry - atr * stop_mult, 0.0)
        take_profit = entry + atr * tp_mult
        trailing_distance = atr * trailing_mult
    elif side == "SHORT":
        stop_loss = entry + atr * stop_mult
        take_profit = max(entry - atr * tp_mult, 0.0)
        trailing_distance = atr * trailing_mult

    trend_score = min(100.0, max(0.0, abs(spread_pct) * 8.0 + (abs(slope_fast) / max(entry, 1e-8)) * 10000.0))
    confidence = 0.5 if signal == "HOLD" else min(0.9, 0.55 + abs(spread_pct) / 10.0)

    return {
        "pair": pair_name,
        "strategy": "trend_following_b31",
        "ready": True,
        "signal": signal,
        "side": side,
        "regime": regime,
        "confidence": round(confidence, 4),
        "trend_score": round(trend_score, 2),
        "entry": round(entry, 8),
        "stop_loss": round(stop_loss, 8) if stop_loss is not None else None,
        "take_profit": round(take_profit, 8) if take_profit is not None else None,
        "trailing_distance": round(trailing_distance, 8) if trailing_distance is not None else None,
        "indicators": {
            "ema_fast": round(fast, 8),
            "ema_slow": round(slow, 8),
            "ema_fast_period": int(ema_fast),
            "ema_slow_period": int(ema_slow),
            "atr": round(atr, 8),
            "atr_period": int(atr_period),
            "spread_pct": round(spread_pct, 6),
            "slope_fast": round(slope_fast, 8),
            "slope_slow": round(slope_slow, 8),
        },
        "risk": {
            "rr_target": float(rr),
            "stop_atr_mult": stop_mult,
            "trailing_atr_mult": trailing_mult,
        },
    }
