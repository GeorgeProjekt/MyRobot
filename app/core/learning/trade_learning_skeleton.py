from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.runtime.trade_journal import get_trade_journal


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _group_key(row: Dict[str, Any], field: str, fallback: str = "unknown") -> str:
    if field in row:
        return _safe_str(row.get(field), fallback)
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    return _safe_str(extra.get(field), fallback)


def _summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    pnl_values = [_safe_float(r.get("pnl")) for r in rows]
    pnl_values = [v for v in pnl_values if v is not None]
    trades = len(rows)
    wins = len([v for v in pnl_values if v > 0])
    losses = len([v for v in pnl_values if v < 0])
    flats = len([v for v in pnl_values if v == 0])
    total_pnl = sum(pnl_values) if pnl_values else 0.0
    avg_pnl = (total_pnl / len(pnl_values)) if pnl_values else 0.0
    avg_win = (sum(v for v in pnl_values if v > 0) / wins) if wins else 0.0
    avg_loss = (sum(v for v in pnl_values if v < 0) / losses) if losses else 0.0
    expectancy = ((wins / trades) * avg_win + (losses / trades) * avg_loss) if trades else 0.0
    size_hint = "normal"
    if expectancy > 0 and trades >= 20 and wins / max(trades, 1) >= 0.55:
        size_hint = "increase"
    elif expectancy < 0 or (trades >= 10 and wins / max(trades, 1) < 0.45):
        size_hint = "reduce"
    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": round((wins / trades) * 100.0, 2) if trades else 0.0,
        "total_pnl": round(total_pnl, 8),
        "avg_pnl": round(avg_pnl, 8),
        "avg_win": round(avg_win, 8),
        "avg_loss": round(avg_loss, 8),
        "expectancy": round(expectancy, 8),
        "size_hint": size_hint,
    }


def build_learning_snapshot(limit: int = 500, journal_dir: Optional[str] = None) -> Dict[str, Any]:
    journal = get_trade_journal(base_dir=journal_dir) if journal_dir else get_trade_journal()
    rows = journal.recent_trades(limit=limit)
    overall = _summarize(rows)

    by_pair = defaultdict(list)
    by_side = defaultdict(list)
    by_strategy = defaultdict(list)

    for row in rows:
        by_pair[_group_key(row, "pair")].append(row)
        by_side[_group_key(row, "side")].append(row)
        strategy = _group_key(row, "strategy")
        if strategy == "unknown":
            extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
            strategy = _safe_str(extra.get("strategy"), "unknown")
        by_strategy[strategy].append(row)

    return {
        "journal_path": str((Path(journal.base_dir) / "trades.jsonl").resolve()),
        "overall": overall,
        "by_pair": {key: _summarize(items) for key, items in sorted(by_pair.items())},
        "by_side": {key: _summarize(items) for key, items in sorted(by_side.items())},
        "by_strategy": {key: _summarize(items) for key, items in sorted(by_strategy.items())},
        "rows_considered": len(rows),
    }
