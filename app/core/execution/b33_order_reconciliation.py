from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.runtime.trade_journal import get_trade_journal


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _pair_parts(pair: str) -> tuple[str, str]:
    raw = str(pair or "").upper().strip()
    if "_" in raw:
        base, quote = raw.split("_", 1)
        return base, quote
    return raw, ""


def _extract_position_from_journal(pair: str, limit: int = 500) -> Dict[str, Any]:
    journal = get_trade_journal()
    trades = journal.recent_trades(limit=limit)
    normalized_pair = str(pair).upper().strip()

    qty = 0.0
    cost_basis = 0.0
    side_state = "FLAT"
    events: List[Dict[str, Any]] = []

    for trade in trades:
        if not isinstance(trade, dict):
            continue
        if str(trade.get("pair") or "").upper().strip() != normalized_pair:
            continue
        side = str(trade.get("side") or "").upper().strip()
        amount = abs(_safe_float(trade.get("amount"), 0.0))
        price = _safe_float(trade.get("price"), 0.0)
        status = str(trade.get("status") or "").lower().strip()
        if amount <= 0 or price <= 0:
            continue
        if status and status not in {"filled", "closed", "executed", "done", "open", "opened"}:
            continue

        if side in {"BUY", "LONG"}:
            qty += amount
            cost_basis += amount * price
        elif side in {"SELL", "SHORT"}:
            qty -= amount
            cost_basis -= amount * price

        side_state = "LONG" if qty > 0 else "SHORT" if qty < 0 else "FLAT"
        events.append(
            {
                "ts": trade.get("ts"),
                "side": side,
                "amount": amount,
                "price": price,
                "status": status or None,
                "qty_after": qty,
            }
        )

    avg_entry = abs(cost_basis / qty) if abs(qty) > 1e-12 else None
    return {
        "pair": normalized_pair,
        "side": side_state,
        "qty": qty,
        "avg_entry": avg_entry,
        "events": events[-25:],
        "open_position": abs(qty) > 1e-12,
    }


def reconcile_pair(
    *,
    pair: str,
    client: Any = None,
    max_gap: float = 0.000001,
) -> Dict[str, Any]:
    normalized_pair = str(pair or "").upper().strip()
    base_ccy, quote_ccy = _pair_parts(normalized_pair)
    local = _extract_position_from_journal(normalized_pair)

    balances = None
    exchange_base = None
    exchange_quote = None
    private_ok = False
    private_error = None

    if client is not None:
        try:
            balances = client.balance_snapshot()
            balances_map = balances.get("balances", {}) if isinstance(balances, dict) else {}
            exchange_base = _safe_float(balances_map.get(base_ccy), 0.0)
            exchange_quote = _safe_float(balances_map.get(quote_ccy), 0.0)
            private_ok = bool(balances.get("ok"))
            if not private_ok:
                private_error = balances.get("error") if isinstance(balances, dict) else "private_snapshot_failed"
        except Exception as exc:
            private_error = str(exc)

    issues: List[str] = []
    severity = "ok"

    local_qty = abs(_safe_float(local.get("qty"), 0.0))
    if client is None:
        issues.append("private_client_unavailable")
        severity = "warning"
    elif not private_ok:
        issues.append("private_balance_snapshot_failed")
        severity = "warning"

    if local.get("side") == "LONG" and exchange_base is not None:
        if abs(exchange_base - local_qty) > float(max_gap):
            issues.append("long_position_exchange_mismatch")
            severity = "critical"

    if local.get("side") == "FLAT" and exchange_base is not None and exchange_base > float(max_gap):
        issues.append("orphan_base_balance_detected")
        severity = "warning"

    if local.get("side") == "SHORT":
        issues.append("short_position_requires_derivatives_or_margin_support")
        severity = "critical"

    return {
        "ok": len(issues) == 0,
        "pair": normalized_pair,
        "severity": severity,
        "issues": issues,
        "local_position": local,
        "exchange_snapshot": {
            "available": client is not None,
            "ok": private_ok,
            "error": private_error,
            "base_currency": base_ccy,
            "quote_currency": quote_ccy,
            "base_balance": exchange_base,
            "quote_balance": exchange_quote,
        },
    }
