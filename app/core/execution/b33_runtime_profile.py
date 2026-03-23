from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PAIRS: List[str] = [
    "BTC_EUR",
    "BTC_CZK",
    "ETH_EUR",
    "ETH_CZK",
    "ADA_CZK",
]


def _normalize_pair(pair: str) -> str:
    return str(pair or "").upper().strip()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_project_config() -> Dict[str, Any]:
    path = _project_root() / "config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _pair_suffix(pair: str) -> str:
    return _normalize_pair(pair).replace("_", "")


def _env_float(name: str) -> Optional[float]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _env_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return None
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def configured_pairs() -> List[str]:
    cfg = load_project_config()
    strategy = cfg.get("strategy", {}) if isinstance(cfg.get("strategy"), dict) else {}
    execution = strategy.get("execution", {}) if isinstance(strategy.get("execution"), dict) else {}
    pairs = execution.get("pairs", []) if isinstance(execution.get("pairs"), list) else []
    out: List[str] = []
    for item in pairs or DEFAULT_PAIRS:
        pair = _normalize_pair(item)
        if pair and pair not in out:
            out.append(pair)
    return out or list(DEFAULT_PAIRS)


def pair_profile(pair: str) -> Dict[str, Any]:
    normalized_pair = _normalize_pair(pair)
    cfg = load_project_config()
    strategy_root = cfg.get("strategy", {}) if isinstance(cfg.get("strategy"), dict) else {}
    b3_3 = cfg.get("b3_3", {}) if isinstance(cfg.get("b3_3"), dict) else {}
    pair_profiles = b3_3.get("pair_profiles", {}) if isinstance(b3_3.get("pair_profiles"), dict) else {}
    pair_cfg = pair_profiles.get(normalized_pair, {}) if isinstance(pair_profiles.get(normalized_pair), dict) else {}

    suffix = _pair_suffix(normalized_pair)
    capital = _env_float(f"PAIR_CAPITAL_{suffix}")
    if capital is None:
        capital = pair_cfg.get("capital")
    if capital is None:
        capital = _env_float("PAIR_CAPITAL_DEFAULT")
    if capital is None:
        capital = float(b3_3.get("default_capital", 0.0) or 0.0)

    strategy_name = (
        os.getenv(f"PAIR_STRATEGY_{suffix}")
        or pair_cfg.get("strategy")
        or strategy_root.get("mode")
        or "trend_following"
    )

    timeframe = (
        os.getenv(f"PAIR_TIMEFRAME_{suffix}")
        or pair_cfg.get("timeframe")
        or b3_3.get("default_timeframe")
        or cfg.get("b3_1", {}).get("timeframe")
        or "1d"
    )

    stale_after_sec = _env_float(f"PAIR_STALE_AFTER_SEC_{suffix}")
    if stale_after_sec is None:
        stale_after_sec = pair_cfg.get("stale_after_sec")
    if stale_after_sec is None:
        stale_after_sec = b3_3.get("default_stale_after_sec", 180.0)
    stale_after_sec = float(stale_after_sec)

    max_reconcile_gap = _env_float(f"PAIR_RECONCILE_GAP_{suffix}")
    if max_reconcile_gap is None:
        max_reconcile_gap = pair_cfg.get("max_reconcile_gap")
    if max_reconcile_gap is None:
        max_reconcile_gap = b3_3.get("default_max_reconcile_gap", 0.000001)
    max_reconcile_gap = float(max_reconcile_gap)

    allow_live = _env_bool(f"PAIR_ALLOW_LIVE_{suffix}")
    if allow_live is None:
        allow_live = bool(pair_cfg.get("allow_live", False))

    return {
        "pair": normalized_pair,
        "capital": float(capital),
        "strategy": str(strategy_name),
        "timeframe": str(timeframe),
        "stale_after_sec": float(stale_after_sec),
        "max_reconcile_gap": float(max_reconcile_gap),
        "allow_live": bool(allow_live),
        "api_key_env": f"COINMATE_API_KEY_{suffix}",
        "api_secret_env": f"COINMATE_API_SECRET_{suffix}",
        "client_id_env": f"COINMATE_CLIENT_ID_{suffix}",
        "has_private_api_env": all(
            bool(os.getenv(name))
            for name in (
                f"COINMATE_API_KEY_{suffix}",
                f"COINMATE_API_SECRET_{suffix}",
                f"COINMATE_CLIENT_ID_{suffix}",
            )
        ) or all(
            bool(os.getenv(name))
            for name in ("COINMATE_API_KEY", "COINMATE_API_SECRET", "COINMATE_CLIENT_ID")
        ),
        "config_source": pair_cfg,
    }


def all_pair_profiles(pairs: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for pair in (pairs or configured_pairs()):
        out[_normalize_pair(pair)] = pair_profile(pair)
    return out
