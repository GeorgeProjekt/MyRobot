from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
import os
import re

from app.storage.db import init_db, kv_get, kv_set

try:
    # single source of truth
    from app.core.strategy_config import load_strategy_config
except Exception:  # pragma: no cover
    load_strategy_config = None  # type: ignore


# --- Timeframes supported by the app (keep in sync with UI/backtest expectations) ---
AVAILABLE_TIMEFRAMES: Tuple[str, ...] = (
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "12h",
    "1d",
)

_PAIR_RE = re.compile(r"^[A-Z0-9]{2,10}_[A-Z0-9]{2,10}$")
_BINANCE_RE = re.compile(r"^[A-Z0-9]{2,10}(/)?[A-Z0-9]{2,10}$")  # BTC/USDT or BTCUSDT


@dataclass
class HealthReport:
    ok: bool
    warnings: List[str]
    errors: List[str]
    details: Dict[str, Any]


def _add_unique(lst: List[str], msg: str) -> None:
    if msg and msg not in lst:
        lst.append(msg)


def _env_present(name: str) -> bool:
    v = os.environ.get(name, "")
    return bool(v and str(v).strip())


def _get_coinmate_key_status() -> Dict[str, bool]:
    # Keep generic so it matches different client implementations
    keys = [
        "COINMATE_API_KEY",
        "COINMATE_API_SECRET",
        "COINMATE_CLIENT_ID",
        "COINMATE_PUBLIC_KEY",
        "COINMATE_PRIVATE_KEY",
    ]
    return {k: _env_present(k) for k in keys}


def validate_timeframe(tf: str, *, warnings: List[str], errors: List[str]) -> None:
    tf = (tf or "").strip()
    if not tf:
        _add_unique(errors, "timeframe: chybí (prázdné).")
        return
    if tf not in AVAILABLE_TIMEFRAMES:
        _add_unique(errors, f"timeframe: '{tf}' není podporovaný. Povolené: {', '.join(AVAILABLE_TIMEFRAMES)}")


def validate_exec_pairs(pairs: Sequence[str], *, warnings: List[str], errors: List[str]) -> None:
    if not pairs:
        _add_unique(errors, "execution.pairs: seznam párů je prázdný.")
        return
    bad: List[str] = []
    for p in pairs:
        p2 = (p or "").strip().upper()
        if not _PAIR_RE.match(p2):
            bad.append(p or "")
    if bad:
        _add_unique(errors, f"execution.pairs: neplatný formát párů: {bad}. Očekávám např. BTC_EUR, ETH_CZK.")


def validate_market_symbols(provider: str, symbols: Sequence[str], *, warnings: List[str], errors: List[str]) -> None:
    if not symbols:
        _add_unique(errors, "market_data.symbols: seznam je prázdný.")
        return

    prov = (provider or "").strip().lower()
    if prov == "binance":
        bad = []
        for s in symbols:
            s2 = (s or "").strip().upper()
            if not _BINANCE_RE.match(s2):
                bad.append(s or "")
        if bad:
            _add_unique(errors, f"market_data.symbols (binance): špatný formát: {bad}. Doporučeno 'BTC/USDT'.")
    elif prov == "coingecko":
        bad = [s for s in symbols if not (isinstance(s, str) and s.strip())]
        if bad:
            _add_unique(errors, "market_data.symbols (coingecko): musí být neprázdné coingecko coin ids.")
    else:
        _add_unique(errors, f"market_data.provider: neznámý provider '{provider}'.")


def validate_risk(cfg: Any, *, warnings: List[str], errors: List[str]) -> None:
    # We keep this tolerant – only hard-fail on obviously invalid values.
    risk_pct = float(getattr(cfg, "risk_pct", 0.0) or 0.0)
    max_positions = int(getattr(cfg, "max_positions", 0) or 0)
    atr_sl_mult = float(getattr(cfg, "atr_sl_mult", 0.0) or 0.0)
    atr_trail_mult = float(getattr(cfg, "atr_trail_mult", 0.0) or 0.0)

    if risk_pct <= 0:
        _add_unique(errors, "risk_pct: musí být > 0 (např. 0.0025 = 0.25%).")
    elif risk_pct > 0.05:
        _add_unique(warnings, f"risk_pct: {risk_pct:.4f} (= {risk_pct*100:.2f}%) je velmi agresivní. Doporučeno <= 1% pro začátek.")

    if max_positions <= 0:
        _add_unique(errors, "max_positions: musí být > 0.")
    elif max_positions > 5:
        _add_unique(warnings, f"max_positions: {max_positions} je vysoké. Zvaž 1–3 pro jednodušší kontrolu rizika.")

    if atr_sl_mult <= 0:
        _add_unique(errors, "atr_sl_mult: musí být > 0.")
    elif atr_sl_mult < 1.0:
        _add_unique(warnings, f"atr_sl_mult: {atr_sl_mult} může být příliš těsné (časté stop-outy).")

    if atr_trail_mult <= 0:
        _add_unique(errors, "atr_trail_mult: musí být > 0.")
    elif atr_trail_mult < 1.0:
        _add_unique(warnings, f"atr_trail_mult: {atr_trail_mult} může být příliš těsné.")


def validate_db(*, warnings: List[str], errors: List[str]) -> None:
    try:
        init_db()
        kv_set("__healthcheck__", "1")
        v = kv_get("__healthcheck__", "")
        if v != "1":
            _add_unique(errors, "DB: kv_set/kv_get nevrátilo očekávanou hodnotu (zkontroluj storage/db).")
    except Exception as e:
        _add_unique(errors, f"DB: chyba při init/kv operaci: {type(e).__name__}: {e}")


def validate_coinmate_keys(*, warnings: List[str], errors: List[str]) -> Dict[str, bool]:
    status = _get_coinmate_key_status()

    has_key_secret = status.get("COINMATE_API_KEY", False) and status.get("COINMATE_API_SECRET", False)
    has_alt = (status.get("COINMATE_PUBLIC_KEY", False) and status.get("COINMATE_PRIVATE_KEY", False)) or status.get("COINMATE_CLIENT_ID", False)

    if not (has_key_secret or has_alt):
        _add_unique(
            warnings,
            "COINMATE klíče: nenalezeny (COINMATE_API_KEY+COINMATE_API_SECRET nebo alternativy). "
            "LIVE obchodování bude automaticky považováno za ne-ready → doporučeno PAPER."
        )
    return status


def _extract_cfg_fields(cfg: Any) -> Dict[str, Any]:
    """Support both new split-config and legacy config."""
    md = getattr(cfg, "market_data", None)
    ex = getattr(cfg, "execution", None)

    if md is not None and ex is not None:
        return {
            "market_provider": getattr(md, "provider", None),
            "market_timeframe": getattr(md, "timeframe", None),
            "market_symbols": getattr(md, "symbols", None),
            "exec_pairs": getattr(ex, "pairs", None),
            "exec_to_data": getattr(md, "exec_to_data", None),
            "mode": getattr(cfg, "mode", None),
            "risk_pct": getattr(cfg, "risk_pct", None),
            "max_positions": getattr(cfg, "max_positions", None),
        }

    # Legacy fallbacks
    return {
        "market_provider": "binance",
        "market_timeframe": getattr(cfg, "timeframe", None),
        "market_symbols": getattr(cfg, "symbols", None),
        "exec_pairs": getattr(cfg, "symbols", None),
        "exec_to_data": {},
        "mode": getattr(cfg, "mode", None),
        "risk_pct": getattr(cfg, "risk_pct", None),
        "max_positions": getattr(cfg, "max_positions", None),
    }


def run_healthcheck(
    config_path: Optional[str] = None,
    *,
    config: Any = None,
    live_armed: Optional[bool] = None,
) -> HealthReport:
    warnings: List[str] = []
    errors: List[str] = []

    cfg = config
    if cfg is None:
        if load_strategy_config is None:
            _add_unique(errors, "Config: nelze importovat load_strategy_config (chybí app.core.strategy_config).")
            cfg = None
        else:
            try:
                cfg = load_strategy_config(config_path)
            except Exception as e:
                _add_unique(errors, f"Config: chyba při načítání konfigurace: {type(e).__name__}: {e}")
                cfg = None

    fields: Dict[str, Any] = {}
    if cfg is not None:
        fields = _extract_cfg_fields(cfg)

        validate_timeframe(str(fields.get("market_timeframe") or ""), warnings=warnings, errors=errors)
        validate_exec_pairs(fields.get("exec_pairs") or [], warnings=warnings, errors=errors)
        validate_market_symbols(
            str(fields.get("market_provider") or ""),
            fields.get("market_symbols") or [],
            warnings=warnings,
            errors=errors,
        )
        validate_risk(cfg, warnings=warnings, errors=errors)
    else:
        _add_unique(errors, "Config: chybí platná konfigurace (cfg == None).")

    validate_db(warnings=warnings, errors=errors)

    key_status = validate_coinmate_keys(warnings=warnings, errors=errors)

    if live_armed is True:
        has_key_secret = key_status.get("COINMATE_API_KEY", False) and key_status.get("COINMATE_API_SECRET", False)
        has_alt = (key_status.get("COINMATE_PUBLIC_KEY", False) and key_status.get("COINMATE_PRIVATE_KEY", False)) or key_status.get("COINMATE_CLIENT_ID", False)
        if not (has_key_secret or has_alt):
            _add_unique(errors, "LIVE ARMED je zapnutý, ale chybí Coinmate klíče. DISARM nebo doplň COINMATE_* env proměnné.")

    ok = len(errors) == 0
    details: Dict[str, Any] = {
        "market_data": {
            "provider": fields.get("market_provider"),
            "timeframe": fields.get("market_timeframe"),
            "symbols": fields.get("market_symbols"),
        },
        "execution": {
            "pairs": fields.get("exec_pairs"),
        },
        # legacy keys retained for older UI expectations
        "timeframe": fields.get("market_timeframe"),
        "symbols": fields.get("exec_pairs"),
        "mode": fields.get("mode"),
        "risk_pct": fields.get("risk_pct"),
        "max_positions": fields.get("max_positions"),
        "coinmate_env_present": key_status,
        "available_timeframes": list(AVAILABLE_TIMEFRAMES),
    }
    return HealthReport(ok=ok, warnings=warnings, errors=errors, details=details)


def as_api_payload(r: HealthReport) -> Dict[str, Any]:
    return {"ok": bool(r.ok), "warnings": r.warnings, "errors": r.errors, "details": r.details}
