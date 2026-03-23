from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.execution.b32_adaptive_risk import build_adaptive_risk_snapshot
from app.core.execution.b32_position_lifecycle import build_position_lifecycle_snapshot
from app.core.execution.coinmate_client import CoinmateClient
from app.core.execution.coinmate_router import CoinmateRouter
from app.core.execution.execution_engine import ExecutionEngine
from app.runtime.trade_journal import get_trade_journal


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_config() -> Dict[str, Any]:
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _venue_capabilities(config: Dict[str, Any]) -> Dict[str, Any]:
    b32 = _safe_dict(config.get("b3_2"))
    caps = _safe_dict(b32.get("venue_capabilities"))
    return {
        "spot_long": bool(caps.get("spot_long", True)),
        "spot_short": bool(caps.get("spot_short", False)),
        "paper_short": bool(caps.get("paper_short", True)),
    }


def _planned_action(plan: Dict[str, Any], position_side: str) -> str:
    signal = str(plan.get("signal") or "").upper().strip()
    desired_side = str(plan.get("side") or "").upper().strip()
    current_side = str(position_side or "FLAT").upper().strip()

    if signal not in {"BUY", "SELL"} or desired_side not in {"LONG", "SHORT"}:
        return "hold"

    if current_side == "FLAT":
        return "enter_long" if desired_side == "LONG" else "enter_short"
    if current_side == desired_side:
        return "scale_in"
    return "flip_position"


def _execution_side_for_action(action: str, desired_side: str) -> Optional[str]:
    if action in {"enter_long", "scale_in"} and desired_side == "LONG":
        return "BUY"
    if action in {"enter_short", "scale_in"} and desired_side == "SHORT":
        return "SELL"
    if action == "flip_position":
        return "BUY" if desired_side == "LONG" else "SELL"
    return None


def build_execution_snapshot(
    pair_name: str,
    plan: Dict[str, Any],
    *,
    learning: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pair = str(pair_name or "").upper().strip()
    config = config if isinstance(config, dict) else _load_config()
    risk = build_adaptive_risk_snapshot(pair, plan, learning=learning, config=config)
    mark_price = _safe_float(_safe_dict(plan).get("entry"), 0.0)
    position = build_position_lifecycle_snapshot(pair, mark_price=mark_price)
    caps = _venue_capabilities(config)

    desired_side = str(_safe_dict(plan).get("side") or "").upper().strip()
    action = _planned_action(plan, _safe_dict(position.get("position")).get("side"))
    execution_side = _execution_side_for_action(action, desired_side)

    venue_supports_action = True
    venue_reason = None
    if desired_side == "LONG" and not caps["spot_long"]:
        venue_supports_action = False
        venue_reason = "venue_long_disabled"
    elif desired_side == "SHORT":
        mode = str(risk.get("execution_mode") or "paper").lower()
        if mode == "live" and not caps["spot_short"]:
            venue_supports_action = False
            venue_reason = "coinmate_spot_short_unsupported"
        elif mode != "live" and not caps["paper_short"]:
            venue_supports_action = False
            venue_reason = "paper_short_disabled"

    order_payload = None
    if risk.get("ok") and venue_supports_action and execution_side:
        order_payload = {
            "pair": pair,
            "side": execution_side,
            "type": "LIMIT",
            "amount": risk.get("recommended_amount"),
            "price": _safe_dict(plan).get("entry"),
            "stop_loss": _safe_dict(plan).get("stop_loss"),
            "take_profit": _safe_dict(plan).get("take_profit"),
            "trailing_distance": _safe_dict(plan).get("trailing_distance"),
            "strategy": _safe_dict(plan).get("strategy"),
            "action": action,
            "intent_side": desired_side,
        }

    execution_ready = bool(order_payload is not None and action != "hold")
    if not execution_ready and venue_reason is None and str(_safe_dict(plan).get("signal")).upper() == "HOLD":
        venue_reason = "strategy_hold"

    return {
        "pair": pair,
        "ready": execution_ready,
        "action": action,
        "desired_side": desired_side or None,
        "execution_side": execution_side,
        "venue_supports_action": venue_supports_action,
        "venue_reason": venue_reason,
        "risk": risk,
        "position": position,
        "order_payload": order_payload,
    }


def execute_pair_plan(
    pair_name: str,
    plan: Dict[str, Any],
    *,
    learning: Optional[Dict[str, Any]] = None,
    mode: str = "paper",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = config if isinstance(config, dict) else _load_config()
    snapshot = build_execution_snapshot(pair_name, plan, learning=learning, config=config)
    journal = get_trade_journal()

    if not snapshot.get("ready"):
        return {
            "ok": False,
            "pair": str(pair_name).upper().strip(),
            "status": "skipped",
            "reason": snapshot.get("venue_reason") or "execution_not_ready",
            "snapshot": snapshot,
        }

    order = _safe_dict(snapshot.get("order_payload"))
    normalized_mode = str(mode or "paper").lower().strip()

    if normalized_mode != "live":
        journal.log_trade(
            pair=pair_name,
            side=str(order.get("side") or ""),
            price=_safe_float(order.get("price"), 0.0),
            amount=_safe_float(order.get("amount"), 0.0),
            mode="paper",
            order_id=f"paper-{pair_name.lower()}",
            status="paper_filled",
            exchange="coinmate",
            execution_ok=True,
            origin="b3_2",
            extra=order,
        )
        return {
            "ok": True,
            "pair": str(pair_name).upper().strip(),
            "status": "paper_filled",
            "mode": "paper",
            "execution_ok": True,
            "snapshot": snapshot,
            "order": order,
        }

    b32 = _safe_dict(config.get("b3_2"))
    execution_cfg = _safe_dict(b32.get("execution"))
    if not bool(execution_cfg.get("allow_live", False)):
        return {
            "ok": False,
            "pair": str(pair_name).upper().strip(),
            "status": "blocked",
            "reason": "live_execution_disabled",
            "snapshot": snapshot,
        }

    client_id = str(os.getenv("COINMATE_CLIENT_ID") or execution_cfg.get("client_id") or "").strip()
    public_key = str(os.getenv("COINMATE_PUBLIC_KEY") or execution_cfg.get("public_key") or "").strip()
    private_key = str(os.getenv("COINMATE_PRIVATE_KEY") or execution_cfg.get("private_key") or "").strip()
    if not (client_id and public_key and private_key):
        return {
            "ok": False,
            "pair": str(pair_name).upper().strip(),
            "status": "blocked",
            "reason": "missing_coinmate_credentials",
            "snapshot": snapshot,
        }

    client = CoinmateClient(
        client_id=client_id,
        public_key=public_key,
        private_key=private_key,
        base_url=str(execution_cfg.get("base_url") or "https://coinmate.io/api"),
        timeout=_safe_float(execution_cfg.get("timeout"), 10.0),
    )
    router = CoinmateRouter(client, pair=str(pair_name).upper().strip())
    engine = ExecutionEngine(router, pair=str(pair_name).upper().strip())
    result = engine.send_order(order)

    journal.log_trade(
        pair=pair_name,
        side=str(order.get("side") or ""),
        price=_safe_float(order.get("price"), 0.0),
        amount=_safe_float(order.get("amount"), 0.0),
        mode="live",
        pnl=None,
        order_id=str(result.get("order_id") or "") or None,
        status=str(result.get("status") or ""),
        exchange="coinmate",
        execution_ok=bool(result.get("execution_ok")),
        origin="b3_2",
        extra={"order": order, "result": result},
    )

    return result
