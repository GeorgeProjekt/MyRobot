# app/core/backtest.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import math

import numpy as np
import pandas as pd

try:
    # project constant (preferred)
    from config import BT_START_EQUITY  # type: ignore
except Exception:
    BT_START_EQUITY = 10_000.0  # safe fallback

from app.core.indicators import Indicators
from app.core.strategy_config import StrategyConfig, load_strategy_config


@dataclass
class BacktestResult:
    ok: bool
    symbol: str
    timeframe: str
    bars: int
    start_equity: float
    end_equity: float
    roi: float
    sharpe: float
    max_drawdown: float
    trades: int
    equity_curve: List[Tuple[int, float]]
    trades_log: List[Dict[str, Any]]
    warnings: List[str]


def _max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    peak = float(equity[0])
    maxdd = 0.0
    for x in equity:
        x = float(x)
        if x > peak:
            peak = x
        dd = (peak - x) / peak if peak > 0 else 0.0
        if dd > maxdd:
            maxdd = dd
    return float(maxdd)


def _sharpe(daily_returns: np.ndarray, annualization: float = 252.0) -> float:
    if daily_returns.size < 3:
        return 0.0
    mu = float(np.nanmean(daily_returns))
    sd = float(np.nanstd(daily_returns, ddof=1))
    if not np.isfinite(sd) or sd <= 1e-12:
        return 0.0
    return float((mu / sd) * math.sqrt(annualization))


def run_backtest(
    *,
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: Optional[StrategyConfig] = None,
    mode: Optional[str] = None,
    risk_pct: Optional[float] = None,
    max_positions: Optional[int] = None,
    ema_fast: Optional[int] = None,
    ema_slow: Optional[int] = None,
    rsi_period: Optional[int] = None,
    rsi_entry: Optional[float] = None,
    atr_period: Optional[int] = None,
    atr_sl_mult: Optional[float] = None,
    atr_trail_mult: Optional[float] = None,
    breakout_n: Optional[int] = None,
    breakout_buffer_atr: Optional[float] = None,
    cooldown_bars: Optional[int] = None,
    fgi_filter: Optional[bool] = None,
    fgi_min: Optional[int] = None,
    fgi_value: Optional[int] = None,
    use_volume: Optional[bool] = None,
    volume_period: Optional[int] = None,
    volume_mult: Optional[float] = None,
    use_resistance: Optional[bool] = None,
    res_lookback: Optional[int] = None,
    res_pivot: Optional[int] = None,
) -> BacktestResult:
    """Config-driven backtest.

    NOTE: If fgi_filter=True but fgi_value is missing, backtest will NOT block all trades.
    Instead it emits a warning and ignores the FGI filter (so you don't get 0 trades silently).
    """
    warnings: List[str] = []

    cfg = config or load_strategy_config()

    tf = (timeframe or getattr(cfg, "market_data", cfg).timeframe if hasattr(cfg, "market_data") else cfg.timeframe)  # type: ignore
    tf = (tf or "1d").strip()
    m = (mode or cfg.mode or "pullback").lower().strip()
    if m not in ("pullback", "breakout"):
        m = "pullback"

    rp = float(risk_pct) if risk_pct is not None else float(cfg.risk_pct)
    mp = int(max_positions) if max_positions is not None else int(cfg.max_positions)

    ef = int(ema_fast) if ema_fast is not None else int(cfg.ema_fast)
    es = int(ema_slow) if ema_slow is not None else int(cfg.ema_slow)

    rper = int(rsi_period) if rsi_period is not None else int(cfg.rsi_period)
    rent = float(rsi_entry) if rsi_entry is not None else float(cfg.rsi_entry)

    aper = int(atr_period) if atr_period is not None else int(cfg.atr_period)
    slm = float(atr_sl_mult) if atr_sl_mult is not None else float(cfg.atr_sl_mult)
    trm = float(atr_trail_mult) if atr_trail_mult is not None else float(cfg.atr_trail_mult)

    bn = int(breakout_n) if breakout_n is not None else int(cfg.breakout_n)
    bbuf = float(breakout_buffer_atr) if breakout_buffer_atr is not None else float(cfg.breakout_buffer_atr)

    cd = int(cooldown_bars) if cooldown_bars is not None else int(cfg.cooldown_bars)

    fgi_on = bool(fgi_filter) if fgi_filter is not None else bool(cfg.fgi_filter)
    fgi_min_v = int(fgi_min) if fgi_min is not None else int(cfg.fgi_min)

    vol_on = bool(use_volume) if use_volume is not None else bool(cfg.use_volume)
    # SAFE volume defaults (avoid 500 when config/UI omit period/mult)
    try:
        vol_p = int(volume_period) if volume_period is not None else int(getattr(cfg, 'volume_period', 20) or 20)
    except Exception:
        vol_p = 20
    try:
        vol_m = float(volume_mult) if volume_mult is not None else float(getattr(cfg, 'volume_mult', 1.2) or 1.2)
    except Exception:
        vol_m = 1.2

    res_on = bool(use_resistance) if use_resistance is not None else bool(cfg.use_resistance)
    res_lb = int(res_lookback) if res_lookback is not None else int(cfg.res_lookback)
    res_piv = int(res_pivot) if res_pivot is not None else int(cfg.res_pivot)

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return BacktestResult(False, symbol, tf, 0, BT_START_EQUITY, BT_START_EQUITY, 0.0, 0.0, 0.0, 0, [], [], warnings)

    df = df.copy()
    required = ("open", "high", "low", "close", "volume", "timestamp")
    for col in required:
        if col not in df.columns:
            return BacktestResult(False, symbol, tf, len(df), BT_START_EQUITY, BT_START_EQUITY, 0.0, 0.0, 0.0, 0, [], [], warnings)

    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(df) < 50:
        return BacktestResult(False, symbol, tf, len(df), BT_START_EQUITY, BT_START_EQUITY, 0.0, 0.0, 0.0, 0, [], [], warnings)

    ind = Indicators()
    df["ema_fast"] = ind.ema(df["close"], ef)
    df["ema_slow"] = ind.ema(df["close"], es)
    df["atr"] = ind.atr(df, aper)

    if m == "pullback":
        df["rsi"] = ind.rsi(df["close"], rper)
    else:
        df["rsi"] = np.nan

    vp = max(int(vol_p), 2)
    df["vol_sma"] = df["volume"].rolling(vp).mean()

    bn2 = max(int(bn), 2)
    df["hh_n"] = df["high"].rolling(bn2).max().shift(1)

    if res_on:
        lb = max(int(res_lb), 10)
        piv = max(int(res_piv), 1)
        highs = df["high"].to_numpy(dtype=float)
        piv_high = np.zeros(len(df), dtype=bool)
        for i in range(piv, len(df) - piv):
            seg = highs[i - piv : i + piv + 1]
            if np.isfinite(highs[i]) and highs[i] == np.nanmax(seg):
                piv_high[i] = True
        res_level = np.full(len(df), np.nan, dtype=float)
        for i in range(len(df)):
            j0 = max(0, i - lb)
            idx = np.where(piv_high[j0 : i + 1])[0]
            if idx.size > 0:
                last = j0 + int(idx[-1])
                res_level[i] = highs[last]
        df["res_level"] = res_level
    else:
        df["res_level"] = np.nan

    equity = float(BT_START_EQUITY)
    start_equity = float(BT_START_EQUITY)
    in_pos = False
    entry_price = 0.0
    stop_price = 0.0
    trail_price = 0.0
    pos_size = 0.0
    last_entry_i = -10_000

    equity_curve: List[Tuple[int, float]] = []
    trades_log: List[Dict[str, Any]] = []

    closes = df["close"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    atrs = df["atr"].to_numpy(dtype=float)
    emaf = df["ema_fast"].to_numpy(dtype=float)
    emas = df["ema_slow"].to_numpy(dtype=float)
    rsi = df["rsi"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    vol_sma = df["vol_sma"].to_numpy(dtype=float)
    hh_n = df["hh_n"].to_numpy(dtype=float)
    res_lvl = df["res_level"].to_numpy(dtype=float)
    def _ts_at(idx: int) -> int:
        """Return a JSON-safe integer timestamp from df (handles numpy/pandas scalars)."""
        try:
            v = df.loc[idx, "timestamp"]
        except Exception:
            return 0
        if v is None:
            return 0
        try:
            if isinstance(v, np.generic):
                v = v.item()
        except Exception:
            pass
        try:
            if isinstance(v, pd.Timestamp):
                return int(v.timestamp())
        except Exception:
            pass
        try:
            return int(v)
        except Exception:
            try:
                return int(float(v))
            except Exception:
                return 0


    def passes_filters(i: int) -> bool:
        # FGI filter: do NOT block backtest if fgi_value is missing; warn instead.
        if fgi_on:
            if fgi_value is None:
                if "FGI filter enabled but fgi_value missing; ignoring FGI for backtest." not in warnings:
                    warnings.append("FGI filter enabled but fgi_value missing; ignoring FGI for backtest.")
            else:
                if int(fgi_value) < int(fgi_min_v):
                    return False

        if vol_on:
            if not (np.isfinite(vol[i]) and np.isfinite(vol_sma[i]) and vol_sma[i] > 0):
                return False
            if float(vol[i]) < float(vol_sma[i]) * float(vol_m):
                return False

        if res_on:
            rl = res_lvl[i]
            if np.isfinite(rl):
                a = atrs[i]
                if np.isfinite(a) and a > 0:
                    if float(rl) - float(closes[i]) < 0.25 * float(a):
                        return False

        return True

    def calc_position_size(i: int, price: float) -> float:
        a = float(atrs[i]) if np.isfinite(atrs[i]) else 0.0
        if a <= 0:
            return 0.0
        stop_dist = float(slm) * a
        if stop_dist <= 0:
            return 0.0
        risk_cash = float(rp) * float(equity)
        units = risk_cash / stop_dist
        max_units = float(equity) / float(price) if price > 0 else 0.0
        return float(max(0.0, min(units, max_units)))

    for i in range(len(df)):
        if in_pos:
            m2m = equity + pos_size * (closes[i] - entry_price)
            equity_curve.append((i, float(m2m)))
        else:
            equity_curve.append((i, float(equity)))

        if i < max(ef, es, aper, bn2) + 5:
            continue

        if in_pos:
            a = float(atrs[i]) if np.isfinite(atrs[i]) else 0.0
            if a > 0:
                new_trail = float(closes[i]) - float(trm) * a
                trail_price = max(trail_price, new_trail)

            effective_stop = max(stop_price, trail_price)

            if float(lows[i]) <= effective_stop:
                exit_price = effective_stop
                pnl = pos_size * (exit_price - entry_price)
                equity += pnl
                trades_log.append({"type": "exit", "i": i, "timestamp": _ts_at(i), "price": float(exit_price),
                                   "pnl": float(pnl), "equity": float(equity), "reason": "stop/trail"})
                in_pos = False
                entry_price = 0.0
                stop_price = 0.0
                trail_price = 0.0
                pos_size = 0.0
                continue

            if m == "pullback":
                if np.isfinite(emaf[i]) and np.isfinite(emas[i]) and float(emaf[i]) < float(emas[i]):
                    exit_price = float(closes[i])
                    pnl = pos_size * (exit_price - entry_price)
                    equity += pnl
                    trades_log.append({"type": "exit", "i": i, "timestamp": _ts_at(i), "price": float(exit_price),
                                       "pnl": float(pnl), "equity": float(equity), "reason": "ema_cross_down"})
                    in_pos = False
                    entry_price = 0.0
                    stop_price = 0.0
                    trail_price = 0.0
                    pos_size = 0.0
                    continue
            else:
                if np.isfinite(emaf[i]) and float(closes[i]) < float(emaf[i]):
                    exit_price = float(closes[i])
                    pnl = pos_size * (exit_price - entry_price)
                    equity += pnl
                    trades_log.append({"type": "exit", "i": i, "timestamp": _ts_at(i), "price": float(exit_price),
                                       "pnl": float(pnl), "equity": float(equity), "reason": "close_below_ema_fast"})
                    in_pos = False
                    entry_price = 0.0
                    stop_price = 0.0
                    trail_price = 0.0
                    pos_size = 0.0
                    continue

            continue

        if cd > 0 and (i - last_entry_i) < cd:
            continue

        if not (np.isfinite(emaf[i]) and np.isfinite(emas[i]) and float(emaf[i]) > float(emas[i])):
            continue

        if not passes_filters(i):
            continue

        entry = False
        reason = ""

        if m == "pullback":
            if np.isfinite(rsi[i]) and float(rsi[i]) <= float(rent):
                entry = True
                reason = "pullback_rsi"
        else:
            if np.isfinite(hh_n[i]) and np.isfinite(atrs[i]):
                level = float(hh_n[i]) + float(bbuf) * float(atrs[i])
                if float(closes[i]) > level:
                    entry = True
                    reason = "breakout_hh"

        if not entry:
            continue

        price = float(closes[i])
        units = calc_position_size(i, price)
        if units <= 0:
            continue

        a = float(atrs[i]) if np.isfinite(atrs[i]) else 0.0
        stop = price - float(slm) * a if a > 0 else price * 0.98
        trail = price - float(trm) * a if a > 0 else stop

        in_pos = True
        entry_price = price
        stop_price = float(stop)
        trail_price = float(trail)
        pos_size = float(units)
        last_entry_i = i

        trades_log.append({"type": "entry", "i": i, "timestamp": _ts_at(i), "price": float(entry_price),
                           "size": float(pos_size), "stop": float(stop_price), "trail": float(trail_price),
                           "equity": float(equity), "reason": reason, "mode": m})

    if in_pos:
        exit_price = float(closes[-1])
        pnl = pos_size * (exit_price - entry_price)
        equity += pnl
        trades_log.append({"type": "exit", "i": len(df) - 1, "timestamp": _ts_at(len(df) - 1),
                           "price": float(exit_price), "pnl": float(pnl), "equity": float(equity), "reason": "eod_close"})

    equity_arr = np.array([e for _, e in equity_curve], dtype=float)
    roi = (float(equity) / float(start_equity) - 1.0) if start_equity > 0 else 0.0
    rets = np.diff(equity_arr) / np.maximum(equity_arr[:-1], 1e-12)
    sharpe = _sharpe(rets)
    maxdd = _max_drawdown(equity_arr)

    return BacktestResult(
        ok=True,
        symbol=str(symbol),
        timeframe=str(tf),
        bars=int(len(df)),
        start_equity=float(start_equity),
        end_equity=float(equity),
        roi=float(roi),
        sharpe=float(sharpe),
        max_drawdown=float(maxdd),
        trades=int(sum(1 for t in trades_log if t.get("type") == "entry")),
        equity_curve=[(int(i), float(v)) for i, v in equity_curve],
        trades_log=trades_log,
        warnings=warnings,
    )
