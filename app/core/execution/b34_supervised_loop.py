from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.control_plane import ControlPlane
from app.core.execution.b32_execution_orchestrator import build_execution_snapshot, execute_pair_plan
from app.core.execution.b33_order_reconciliation import reconcile_pair
from app.core.execution.b33_runtime_profile import configured_pairs, pair_profile, load_project_config
from app.core.execution.b33_stale_data_guard import build_stale_data_snapshot
from app.core.execution.b34_order_state_machine import build_order_state_snapshot, log_order_event, transition_state
from app.core.execution.b34_retry_policy import build_retry_decision
from app.core.execution.b34_watchdog_alerts import build_watchdog_alerts
from app.core.learning.trade_learning_skeleton import build_learning_snapshot
from app.core.market.coinmate_feed import load_market_snapshot
from app.core.strategy.trend_following_b31 import build_trend_following_plan
from app.services.robot_service import load_coinmate_creds_from_env
from app.core.execution.coinmate_client import CoinmateClient


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_b34_config() -> Dict[str, Any]:
    cfg = load_project_config()
    node = cfg.get("b3_4")
    return node if isinstance(node, dict) else {}


def _client_for_pair(pair: str):
    creds = load_coinmate_creds_from_env(pair)
    if not isinstance(creds, dict):
        return None
    api_key = str(creds.get("api_key") or "").strip()
    api_secret = str(creds.get("api_secret") or "").strip()
    client_id = str(creds.get("client_id") or "").strip()
    if not (api_key and api_secret and client_id):
        return None
    try:
        return CoinmateClient(api_key=api_key, api_secret=api_secret, client_id=client_id)
    except Exception:
        return None


def _loop_log_path() -> Path:
    path = _project_root() / "runtime" / "journal" / "supervised_loop.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _append_loop(payload: Dict[str, Any]) -> None:
    with _loop_log_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _loop_health(pair: str, stale_guard: Dict[str, Any], control: Dict[str, Any], execution_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    blocked = bool(_safe_dict(control).get("kill_switch")) or bool(_safe_dict(stale_guard).get("stale"))
    ready = bool(_safe_dict(execution_snapshot).get("ready"))
    loop_ok = not blocked
    return {
        "pair": pair,
        "loop_ok": loop_ok,
        "blocked": blocked,
        "ready_to_execute": ready,
        "blocked_reason": (
            "kill_switch" if bool(_safe_dict(control).get("kill_switch")) else
            "stale_data" if bool(_safe_dict(stale_guard).get("stale")) else
            None
        ),
    }


def build_pair_loop_snapshot(
    pair: str,
    *,
    timeframe: Optional[str] = None,
    days: int = 90,
    control_plane: Optional[ControlPlane] = None,
) -> Dict[str, Any]:
    profile = pair_profile(pair)
    tf = str(timeframe or profile.get("timeframe") or "1d")
    control = (control_plane or ControlPlane()).as_dict(pair=pair)
    market = load_market_snapshot(pair, timeframe=tf, days=int(days), include_private=False)
    candles = _safe_dict(market.get("chart")).get("candles", []) or []
    plan = build_trend_following_plan(pair, candles)
    learning = build_learning_snapshot(limit=int(_load_b34_config().get("learning_limit", 500) or 500))
    execution = build_execution_snapshot(pair, plan, learning=learning, config=load_project_config())
    stale = build_stale_data_snapshot(market, stale_after_sec=float(profile.get("stale_after_sec") or 180.0))
    client = _client_for_pair(pair)
    reconciliation = reconcile_pair(pair=pair, client=client, max_gap=float(profile.get("max_reconcile_gap") or 0.000001))
    order_state = build_order_state_snapshot(pair)
    loop_health = _loop_health(pair, stale, control, execution)
    watchdog = build_watchdog_alerts(
        pair=pair,
        profile=profile,
        stale_guard=stale,
        reconciliation=reconciliation,
        control=control,
        order_state=order_state,
        loop_health=loop_health,
    )
    return {
        "ok": True,
        "pair": pair,
        "profile": profile,
        "control": control,
        "market": market,
        "plan": plan,
        "learning": learning,
        "execution": execution,
        "stale_guard": stale,
        "reconciliation": reconciliation,
        "order_state": order_state,
        "loop_health": loop_health,
        "watchdog": watchdog,
    }


def supervised_loop_step(
    pair: str,
    *,
    timeframe: Optional[str] = None,
    days: int = 90,
    execute: bool = False,
    control_plane: Optional[ControlPlane] = None,
) -> Dict[str, Any]:
    cp = control_plane or ControlPlane()
    snap = build_pair_loop_snapshot(pair, timeframe=timeframe, days=days, control_plane=cp)
    state = _safe_dict(_safe_dict(snap.get("order_state")).get("current"))
    current_state = str(state.get("state") or "planned")
    execution = _safe_dict(snap.get("execution"))
    order_payload = _safe_dict(execution.get("order_payload"))
    max_attempts = int(_safe_dict(_load_b34_config().get("retry_policy")).get("max_attempts", 3) or 3)

    blocked = bool(_safe_dict(snap.get("control")).get("kill_switch")) or bool(_safe_dict(snap.get("stale_guard")).get("stale"))
    simulated = None
    if blocked:
        transition_state(pair=pair, order_id=None, current_state=current_state, event="fail", reason="blocked_by_supervisor", mode=str(execution.get("mode") or "paper"))
    elif order_payload:
        log_order_event(
            pair=pair,
            order_id=str(order_payload.get("client_order_id") or order_payload.get("pair") + "-planned"),
            state="planned",
            event="plan",
            side=order_payload.get("side"),
            amount=_safe_float(order_payload.get("amount")),
            price=_safe_float(order_payload.get("price")),
            mode=str(execution.get("mode") or "paper"),
            extra={"desired_side": execution.get("desired_side"), "action": execution.get("action")},
        )
        transition_state(
            pair=pair,
            order_id=str(order_payload.get("client_order_id") or order_payload.get("pair") + "-submit"),
            current_state="planned",
            event="submit",
            side=order_payload.get("side"),
            amount=_safe_float(order_payload.get("amount")),
            price=_safe_float(order_payload.get("price")),
            mode=str(execution.get("mode") or "paper"),
        )
        if execute:
            result = execute_pair_plan(pair, _safe_dict(snap.get("plan")), learning=_safe_dict(snap.get("learning")), mode=str(_load_b34_config().get("mode") or "paper"), config=load_project_config())
            simulated = result
            if result.get("ok"):
                transition_state(
                    pair=pair,
                    order_id=str(result.get("order_id") or order_payload.get("client_order_id") or pair + "-exec"),
                    current_state="submitted",
                    event="accept",
                    side=order_payload.get("side"),
                    amount=_safe_float(order_payload.get("amount")),
                    price=_safe_float(order_payload.get("price")),
                    mode=str(execution.get("mode") or "paper"),
                    extra={"execution": result},
                )
            else:
                retry = build_retry_decision(result, pair=pair, attempt=int(result.get("attempt") or 1), max_attempts=max_attempts, base_delay_sec=float(_safe_dict(_load_b34_config().get("retry_policy")).get("base_delay_sec", 0.75) or 0.75))
                event = "retry" if retry.get("should_retry") else "fail"
                transition_state(
                    pair=pair,
                    order_id=str(order_payload.get("client_order_id") or pair + "-exec"),
                    current_state="submitted",
                    event=event,
                    side=order_payload.get("side"),
                    amount=_safe_float(order_payload.get("amount")),
                    price=_safe_float(order_payload.get("price")),
                    mode=str(execution.get("mode") or "paper"),
                    reason=str(retry.get("reason") or "execution_failed"),
                    extra={"execution": result, "retry": retry},
                )

    final_snap = build_pair_loop_snapshot(pair, timeframe=timeframe, days=days, control_plane=cp)
    final_snap["step"] = {
        "executed": bool(execute),
        "blocked": blocked,
        "result": simulated,
    }
    _append_loop({
        "ts": datetime.now(timezone.utc).isoformat(),
        "pair": pair,
        "blocked": blocked,
        "execute": bool(execute),
        "watchdog_severity": _safe_dict(final_snap.get("watchdog")).get("severity"),
        "loop_health": final_snap.get("loop_health"),
    })
    return final_snap


def supervised_loop_overview(
    *,
    pairs: Optional[List[str]] = None,
    timeframe: Optional[str] = None,
    days: int = 90,
    control_plane: Optional[ControlPlane] = None,
) -> Dict[str, Any]:
    selected = pairs or configured_pairs()
    cp = control_plane or ControlPlane()
    states: Dict[str, Any] = {}
    blocked = 0
    warnings = 0
    for pair in selected:
        snap = build_pair_loop_snapshot(pair, timeframe=timeframe, days=days, control_plane=cp)
        states[pair] = {
            "profile": snap.get("profile"),
            "control": snap.get("control"),
            "loop_health": snap.get("loop_health"),
            "watchdog": snap.get("watchdog"),
            "order_state": snap.get("order_state"),
            "execution": {
                "can_execute": _safe_dict(snap.get("execution")).get("ready"),
                "action": _safe_dict(snap.get("execution")).get("action"),
                "mode": _safe_dict(snap.get("execution")).get("mode"),
            },
        }
        if _safe_dict(snap.get("loop_health")).get("blocked"):
            blocked += 1
        if str(_safe_dict(snap.get("watchdog")).get("severity") or "info") in {"warning", "critical"}:
            warnings += 1
    return {
        "ok": True,
        "mode": str(_load_b34_config().get("mode") or "paper"),
        "pairs": selected,
        "blocked_pairs": blocked,
        "pairs_with_alerts": warnings,
        "states": states,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
