from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.learning.trade_learning_skeleton import build_learning_snapshot


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_config() -> Dict[str, Any]:
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _quote_ccy(pair_name: str) -> str:
    pair = str(pair_name or "").upper().strip()
    if "_" in pair:
        parts = pair.split("_", 1)
        if len(parts) == 2 and parts[1]:
            return parts[1]
    return "EUR"


def _paper_equity_for_pair(pair_name: str, config: Dict[str, Any]) -> float:
    strategy = _safe_dict(config.get("strategy"))
    balances = _safe_dict(strategy.get("paper_balances"))
    quote = _quote_ccy(pair_name)
    eq = _safe_float(balances.get(quote), 0.0)
    if eq > 0:
        return eq
    if balances:
        for value in balances.values():
            eq = _safe_float(value, 0.0)
            if eq > 0:
                return eq
    return 10000.0


def _pair_learning_summary(pair_name: str, learning: Dict[str, Any]) -> Dict[str, Any]:
    pair = str(pair_name or "").upper().strip()
    pair_stats = _safe_dict(_safe_dict(learning.get("by_pair")).get(pair))
    strategy_stats = _safe_dict(_safe_dict(learning.get("by_strategy")).get("trend_following_b31"))
    return {
        "pair": pair_stats,
        "strategy": strategy_stats,
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_adaptive_risk_snapshot(
    pair_name: str,
    plan: Dict[str, Any],
    *,
    learning: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pair = str(pair_name or "").upper().strip()
    plan = _safe_dict(plan)
    if config is None:
        config = _load_config()
    if learning is None:
        learning = build_learning_snapshot(limit=_safe_int(_safe_dict(config.get("b3_1")).get("learning_limit"), 500))

    strategy_cfg = _safe_dict(config.get("strategy"))
    risk_cfg = _safe_dict(config.get("b3_2"))
    learning_summary = _pair_learning_summary(pair, learning)

    base_risk_pct = _safe_float(strategy_cfg.get("risk_pct"), 0.005)
    max_position_value_pct = _safe_float(strategy_cfg.get("max_position_value_pct"), 0.30)
    min_trade_value = _safe_float(strategy_cfg.get("min_trade_value"), 10.0)
    fee_bps = _safe_float(strategy_cfg.get("fee_bps"), 8.0)
    slippage_bps = _safe_float(strategy_cfg.get("slippage_bps"), 5.0)

    paper_equity = _paper_equity_for_pair(pair, config)
    entry = _safe_float(plan.get("entry"), 0.0)
    stop_loss = _safe_float(plan.get("stop_loss"), 0.0)
    confidence = _safe_float(plan.get("confidence"), 0.5)
    trend_score = _safe_float(plan.get("trend_score"), 0.0)

    pair_stats = learning_summary["pair"]
    strategy_stats = learning_summary["strategy"]

    pair_expectancy = _safe_float(pair_stats.get("expectancy"), 0.0)
    pair_win_rate = _safe_float(pair_stats.get("win_rate"), _safe_float(strategy_stats.get("win_rate"), 0.5))
    pair_trades = _safe_int(pair_stats.get("trades"), 0)
    strategy_expectancy = _safe_float(strategy_stats.get("expectancy"), 0.0)
    strategy_trades = _safe_int(strategy_stats.get("trades"), 0)

    confidence_mult = _clamp(0.65 + confidence * 0.7, 0.50, 1.25)
    trend_mult = _clamp(0.65 + (trend_score / 100.0) * 0.75, 0.50, 1.40)

    expectancy_source = pair_expectancy if pair_trades >= 5 else strategy_expectancy
    expectancy_mult = 1.0
    if expectancy_source > 0:
        expectancy_mult = _clamp(1.0 + expectancy_source * 2.0, 0.85, 1.35)
    elif expectancy_source < 0:
        expectancy_mult = _clamp(1.0 + expectancy_source * 1.5, 0.55, 1.0)

    sample_mult = 1.0 if pair_trades >= 10 else (0.85 if strategy_trades >= 10 else 0.75)
    win_rate_mult = _clamp(0.75 + pair_win_rate, 0.70, 1.30)

    adaptive_risk_pct = base_risk_pct * confidence_mult * trend_mult * expectancy_mult * sample_mult * win_rate_mult
    adaptive_risk_pct = _clamp(adaptive_risk_pct, base_risk_pct * 0.35, base_risk_pct * 1.75)

    stop_distance = abs(entry - stop_loss) if entry > 0 and stop_loss > 0 else 0.0
    risk_budget = paper_equity * adaptive_risk_pct
    max_position_value = paper_equity * max_position_value_pct

    raw_amount = (risk_budget / stop_distance) if stop_distance > 0 else 0.0
    max_amount_by_notional = (max_position_value / entry) if entry > 0 else 0.0
    recommended_amount = min(raw_amount, max_amount_by_notional) if raw_amount > 0 and max_amount_by_notional > 0 else 0.0

    estimated_notional = recommended_amount * entry
    estimated_cost = estimated_notional * (fee_bps + slippage_bps) / 10000.0

    risk_ok = bool(
        plan.get("ready")
        and str(plan.get("signal")).upper() in {"BUY", "SELL"}
        and entry > 0
        and stop_distance > 0
        and estimated_notional >= min_trade_value
        and recommended_amount > 0
    )

    mode = str(_safe_dict(risk_cfg.get("execution")).get("mode") or "paper").lower()
    allow_live = bool(_safe_dict(risk_cfg.get("execution")).get("allow_live", False))

    return {
        "pair": pair,
        "ok": risk_ok,
        "execution_mode": mode,
        "allow_live": allow_live,
        "paper_equity": round(paper_equity, 8),
        "base_risk_pct": round(base_risk_pct, 8),
        "adaptive_risk_pct": round(adaptive_risk_pct, 8),
        "risk_budget": round(risk_budget, 8),
        "max_position_value": round(max_position_value, 8),
        "entry": round(entry, 8) if entry > 0 else None,
        "stop_loss": round(stop_loss, 8) if stop_loss > 0 else None,
        "stop_distance": round(stop_distance, 8) if stop_distance > 0 else None,
        "recommended_amount": round(recommended_amount, 8),
        "recommended_notional": round(estimated_notional, 8),
        "estimated_cost": round(estimated_cost, 8),
        "min_trade_value": round(min_trade_value, 8),
        "constraints": {
            "meets_min_trade_value": bool(estimated_notional >= min_trade_value),
            "within_position_cap": bool(estimated_notional <= max_position_value + 1e-9),
            "has_valid_stop": bool(stop_distance > 0),
        },
        "learning": {
            "pair_expectancy": round(pair_expectancy, 8),
            "pair_win_rate": round(pair_win_rate, 8),
            "pair_trades": int(pair_trades),
            "strategy_expectancy": round(strategy_expectancy, 8),
            "strategy_trades": int(strategy_trades),
        },
        "multipliers": {
            "confidence": round(confidence_mult, 6),
            "trend": round(trend_mult, 6),
            "expectancy": round(expectancy_mult, 6),
            "sample": round(sample_mult, 6),
            "win_rate": round(win_rate_mult, 6),
        },
        "reason": None if risk_ok else "risk_constraints_not_met",
    }
