
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional
import time
import inspect

# We intentionally import run_backtest lazily (inside functions) to avoid circular imports in some setups.


@dataclass
class KellyStats:
    trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    rr: float
    kelly: float
    kelly_fractional: float


def kelly_fraction(win_rate: float, rr: float) -> float:
    """Classic Kelly fraction for a binary outcome with variable payoff.
    win_rate: probability of positive PnL
    rr: average_win / average_loss  (loss as positive magnitude)
    returns in [0, 1]
    """
    try:
        w = float(win_rate)
        r = float(rr)
    except Exception:
        return 0.0
    if r <= 0:
        return 0.0
    k = w - ((1.0 - w) / r)
    if k < 0:
        return 0.0
    if k > 1:
        return 1.0
    return float(k)


def _bt_result_to_dict(res: Any) -> Dict[str, Any]:
    if res is None:
        return {}
    if isinstance(res, dict):
        return res
    if hasattr(res, "model_dump") and callable(getattr(res, "model_dump")):
        try:
            return dict(res.model_dump())
        except Exception:
            pass
    if hasattr(res, "dict") and callable(getattr(res, "dict")):
        try:
            return dict(res.dict())
        except Exception:
            pass
    if hasattr(res, "__dict__"):
        try:
            return dict(res.__dict__)
        except Exception:
            pass
    try:
        return dict(vars(res))
    except Exception:
        return {}


def _call_run_backtest_sig_safe(**kwargs) -> Any:
    """Call app.core.backtest.run_backtest while filtering kwargs by signature.
    This mirrors the pattern used in main.py so we don't break when the signature evolves.
    """
    from app.core.backtest import run_backtest  # local import

    sig = inspect.signature(run_backtest)
    allowed = set(sig.parameters.keys())
    payload = {k: v for k, v in kwargs.items() if k in allowed}
    return run_backtest(**payload)


def kelly_from_trades_log(
    trades_log: List[Dict[str, Any]],
    *,
    fractional: float = 0.25,
    min_trades: int = 30,
) -> KellyStats:
    """Estimate Kelly from backtest trades_log.

    Expects trades_log entries to contain 'pnl' on exits (your backtest does this).
    If there are not enough trades, returns kelly_fractional=0 (so you fall back to base risk).
    """
    pnls: List[float] = []
    for t in trades_log or []:
        try:
            pnl = t.get("pnl")
            if pnl is None:
                continue
            pnl = float(pnl)
            if pnl != pnl:  # NaN
                continue
            pnls.append(pnl)
        except Exception:
            continue

    n = len(pnls)
    if n < int(min_trades):
        return KellyStats(
            trades=n,
            win_rate=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            rr=0.0,
            kelly=0.0,
            kelly_fractional=0.0,
        )

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_rate = float(len(wins) / n) if n else 0.0
    avg_win = float(sum(wins) / len(wins)) if wins else 0.0
    avg_loss = float(abs(sum(losses) / len(losses))) if losses else 0.0

    rr = float(avg_win / avg_loss) if (avg_win > 0 and avg_loss > 0) else 0.0
    k = kelly_fraction(win_rate, rr)
    frac = float(max(0.0, min(float(fractional), 1.0)))

    return KellyStats(
        trades=n,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        rr=rr,
        kelly=k,
        kelly_fractional=float(k * frac),
    )


def kelly_from_backtest(
    *,
    df: Any,
    symbol: str,
    timeframe: str,
    risk_pct: float,
    mode: str,
    use_volume: Optional[bool] = None,
    volume_mult: Optional[float] = None,
    use_resistance: Optional[bool] = None,
    res_lookback: Optional[int] = None,
    res_pivot: Optional[int] = None,
    fgi_value: Optional[int] = None,
    fractional: float = 0.25,
    min_trades: int = 30,
) -> KellyStats:
    """Runs the same backtest logic and derives Kelly from the produced PnLs."""
    res = _call_run_backtest_sig_safe(
        df=df,
        symbol=symbol,
        timeframe=timeframe,
        risk_pct=float(risk_pct),
        mode=str(mode or "breakout"),
        use_volume=use_volume,
        volume_mult=volume_mult,
        use_resistance=use_resistance,
        res_lookback=res_lookback,
        res_pivot=res_pivot,
        fgi_value=fgi_value,
    )
    resd = _bt_result_to_dict(res)
    trades_log = resd.get("trades_log") or []
    return kelly_from_trades_log(trades_log, fractional=fractional, min_trades=min_trades)


def portfolio_kelly_factor(
    kelly_stats: List[KellyStats],
    *,
    default_factor: float = 1.0,
    min_trades_total: int = 60,
) -> Tuple[float, Dict[str, Any]]:
    """Combine per-symbol Kelly into one risk multiplier.
    We take a weighted average by number of trades. Returns factor in [0,1] typically.
    If insufficient data, returns default_factor.
    """
    total_trades = sum(s.trades for s in kelly_stats)
    if total_trades < int(min_trades_total):
        return float(default_factor), {"reason": "insufficient_trades", "total_trades": int(total_trades)}

    num = 0.0
    den = 0.0
    for s in kelly_stats:
        if s.trades <= 0:
            continue
        num += float(s.kelly_fractional) * float(s.trades)
        den += float(s.trades)

    if den <= 0:
        return float(default_factor), {"reason": "no_trades", "total_trades": int(total_trades)}

    # This is the multiplier to apply to base risk_pct. Clamp for safety.
    factor = num / den
    if factor < 0:
        factor = 0.0
    if factor > 1.0:
        factor = 1.0

    return float(factor), {
        "total_trades": int(total_trades),
        "factor": float(factor),
        "per_symbol": [s.__dict__ for s in kelly_stats],
    }
