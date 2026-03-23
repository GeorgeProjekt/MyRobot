from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.core.engine.position_manager import PositionManager
from app.runtime.trade_journal import get_trade_journal


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_trade_amount(trade: Dict[str, Any]) -> float:
    amount = _safe_float(trade.get("amount"), 0.0)
    extra = _safe_dict(trade.get("extra"))
    if amount > 0:
        return amount
    amount = _safe_float(extra.get("filled"), 0.0)
    if amount > 0:
        return amount
    amount = _safe_float(extra.get("requested_amount"), 0.0)
    return max(amount, 0.0)


def _normalize_trade_price(trade: Dict[str, Any]) -> float:
    price = _safe_float(trade.get("price"), 0.0)
    extra = _safe_dict(trade.get("extra"))
    if price > 0:
        return price
    price = _safe_float(extra.get("avg_price"), 0.0)
    if price > 0:
        return price
    return _safe_float(extra.get("requested_price"), 0.0)


def _trade_side(trade: Dict[str, Any]) -> str:
    side = str(trade.get("side") or "").upper().strip()
    return side if side in {"BUY", "SELL"} else ""


def _trade_status(trade: Dict[str, Any]) -> str:
    return str(trade.get("status") or "").lower().strip()


def _iter_pair_trades(pair_name: str, limit: int = 500) -> List[Dict[str, Any]]:
    journal = get_trade_journal()
    trades = journal.recent_trades(limit=limit)
    pair = str(pair_name or "").upper().strip()
    out: List[Dict[str, Any]] = []
    for trade in trades:
        if str(trade.get("pair") or "").upper().strip() == pair:
            out.append(trade)
    return out


def build_position_lifecycle_snapshot(
    pair_name: str,
    *,
    mark_price: Optional[float] = None,
    limit: int = 500,
) -> Dict[str, Any]:
    pair = str(pair_name or "").upper().strip()
    trades = _iter_pair_trades(pair, limit=limit)

    pm = PositionManager(pair)
    opens = 0
    closes = 0
    flips = 0
    reductions = 0
    increases = 0
    last_fill: Optional[Dict[str, Any]] = None
    closed_trades: List[Dict[str, Any]] = []

    for trade in trades:
        status = _trade_status(trade)
        if status and status not in {"filled", "closed", "executed", "submitted", "paper_filled"}:
            continue
        side = _trade_side(trade)
        amount = _normalize_trade_amount(trade)
        price = _normalize_trade_price(trade)
        if side not in {"BUY", "SELL"} or amount <= 0 or price <= 0:
            continue

        before = pm.side()
        result = pm.apply_fill(side, amount, price)
        after = pm.side()
        if not result.get("ok"):
            continue

        event = str(result.get("event") or "")
        if event == "opened":
            opens += 1
        elif event == "closed":
            closes += 1
            closed_trades.append({"trade": trade, "result": result})
        elif event == "flipped":
            flips += 1
        elif event == "reduced":
            reductions += 1
        elif event == "increased":
            increases += 1

        last_fill = {
            "side": side,
            "amount": amount,
            "price": price,
            "status": status or "filled",
            "before": before,
            "after": after,
            "ts": trade.get("ts"),
            "order_id": trade.get("order_id"),
        }

    if mark_price is not None:
        pm.mark_price(_safe_float(mark_price, 0.0))

    position = pm.snapshot()
    lifecycle_state = "flat"
    if position.get("is_open"):
        lifecycle_state = "open_long" if position.get("side") == "LONG" else "open_short"

    return {
        "pair": pair,
        "position": position,
        "lifecycle": {
            "state": lifecycle_state,
            "open_events": int(opens),
            "close_events": int(closes),
            "flip_events": int(flips),
            "increase_events": int(increases),
            "reduction_events": int(reductions),
            "last_fill": last_fill,
            "closed_trade_count": len(closed_trades),
        },
        "journal_stats": {
            "recent_trade_events": len(trades),
        },
    }
