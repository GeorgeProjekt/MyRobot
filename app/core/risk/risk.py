from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class RiskConfig:
    """
    Central deterministic risk configuration.
    All percentages are fractions, e.g. 0.01 = 1%.
    """

    default_risk_pct: float = 0.005

    max_position_value_pct: float = 0.20
    max_total_exposure_pct: float = 0.80
    max_trade_value: float = 1_000_000.0
    min_trade_value: float = 10.0

    max_drawdown_pct: float = 0.15
    drawdown_cooldown_seconds: float = 300.0
    min_equity_to_track: float = 1.0

    max_daily_loss_pct: float = 0.05
    max_consecutive_failures: int = 5

    fee_pct: float = 0.001
    slippage_pct: float = 0.001

    min_size: float = 0.0
    max_size: float = 0.0

    block_if_atr_missing: bool = True
    allow_sizing_without_quote_balance: bool = False

    # Stop-loss aware sizing
    default_stop_loss_pct: float = 0.01

    # ATR fallback
    atr_fallback_pct_of_price: float = 0.005
    atr_fallback_min_abs: float = 0.0

    # Adaptive slippage
    adaptive_slippage_atr_mult: float = 0.10
    adaptive_slippage_max_pct: float = 0.01

    # Risk pressure score weights
    risk_pressure_drawdown_weight: float = 0.35
    risk_pressure_daily_loss_weight: float = 0.30
    risk_pressure_exposure_weight: float = 0.20
    risk_pressure_failures_weight: float = 0.15


@dataclass
class _DrawdownState:
    peak_equity: float = 0.0
    last_equity: float = 0.0
    drawdown: float = 0.0
    max_drawdown_seen: float = 0.0

    halted: bool = False
    halt_reason: Optional[str] = None
    cooldown_until_ts: Optional[float] = None

    last_day_key: Optional[str] = None
    day_start_equity: float = 0.0
    daily_loss_pct: float = 0.0

    consecutive_failures: int = 0


class _DrawdownTracker:
    """
    Internal persistent drawdown / cooldown tracker.
    """

    def __init__(self, cfg: RiskConfig) -> None:
        self.cfg = cfg
        self.state = _DrawdownState()

    def update(self, equity: float, now_ts: Optional[float] = None) -> _DrawdownState:
        now = float(now_ts if now_ts is not None else time.time())
        eq = float(equity or 0.0)
        self.state.last_equity = eq

        self._update_day_state(eq, now)

        if eq < self.cfg.min_equity_to_track:
            self.state.drawdown = 0.0
            self._update_halt_status(now)
            return self.state

        if self.state.peak_equity <= 0.0:
            self.state.peak_equity = eq
            self.state.drawdown = 0.0
            self._update_halt_status(now)
            return self.state

        if eq > self.state.peak_equity:
            self.state.peak_equity = eq

        peak = self.state.peak_equity
        dd = 0.0 if peak <= 0.0 else max(0.0, (peak - eq) / peak)

        self.state.drawdown = float(dd)
        if dd > self.state.max_drawdown_seen:
            self.state.max_drawdown_seen = float(dd)

        self._apply_policy(now)
        return self.state

    def register_failure(self, now_ts: Optional[float] = None) -> None:
        now = float(now_ts if now_ts is not None else time.time())
        self.state.consecutive_failures += 1

        if self.cfg.max_consecutive_failures > 0 and self.state.consecutive_failures >= self.cfg.max_consecutive_failures:
            self.state.halted = True
            self.state.halt_reason = (
                f"max_consecutive_failures_exceeded "
                f"({self.state.consecutive_failures} >= {self.cfg.max_consecutive_failures})"
            )
            if self.cfg.drawdown_cooldown_seconds > 0:
                self.state.cooldown_until_ts = now + self.cfg.drawdown_cooldown_seconds

    def clear_failures(self) -> None:
        self.state.consecutive_failures = 0

    def _update_day_state(self, equity: float, now_ts: float) -> None:
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now_ts))

        if self.state.last_day_key != day_key:
            self.state.last_day_key = day_key
            self.state.day_start_equity = max(float(equity or 0.0), 0.0)
            self.state.daily_loss_pct = 0.0

        start_eq = float(self.state.day_start_equity or 0.0)
        if start_eq > 0.0 and equity > 0.0:
            self.state.daily_loss_pct = max(0.0, (start_eq - equity) / start_eq)
        else:
            self.state.daily_loss_pct = 0.0

    def _apply_policy(self, now_ts: float) -> None:
        self._update_halt_status(now_ts)
        if self.state.halted:
            return

        if self.cfg.max_drawdown_pct > 0 and self.state.drawdown >= self.cfg.max_drawdown_pct:
            self.state.halted = True
            self.state.halt_reason = (
                f"max_drawdown_exceeded ({self.state.drawdown:.4f} >= {self.cfg.max_drawdown_pct:.4f})"
            )
            if self.cfg.drawdown_cooldown_seconds > 0:
                self.state.cooldown_until_ts = now_ts + self.cfg.drawdown_cooldown_seconds
            return

        if self.cfg.max_daily_loss_pct > 0 and self.state.daily_loss_pct >= self.cfg.max_daily_loss_pct:
            self.state.halted = True
            self.state.halt_reason = (
                f"max_daily_loss_exceeded ({self.state.daily_loss_pct:.4f} >= {self.cfg.max_daily_loss_pct:.4f})"
            )
            if self.cfg.drawdown_cooldown_seconds > 0:
                self.state.cooldown_until_ts = now_ts + self.cfg.drawdown_cooldown_seconds

    def _update_halt_status(self, now_ts: float) -> None:
        until = self.state.cooldown_until_ts
        if until is None:
            return

        if now_ts >= float(until):
            self.state.halted = False
            self.state.halt_reason = None
            self.state.cooldown_until_ts = None
            self.state.consecutive_failures = 0


class RiskManager:
    """
    Central deterministic risk manager.

    Public API:
    - position_size(...)
    - size_and_diag(...)
    - validate_and_adjust(...)
    - note_equity(...)
    - note_execution_failure(...)
    - note_execution_success(...)
    - diagnostics(...)
    """

    def __init__(
        self,
        config: Optional[RiskConfig] = None,
        *,
        pair: Optional[str] = None,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.config = config or RiskConfig()
        self.pair = str(pair or "GLOBAL").upper().strip()

        self._lock = RLock()
        self._dd = _DrawdownTracker(self.config)

        base_dir = Path(state_dir or (Path("runtime") / "risk")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_risk_state.json"

        self._load_state()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        with self._lock:
            if not self._state_file.exists():
                return

            try:
                payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            except Exception:
                # corrupted file -> keep clean in-memory default state
                self._dd.state = _DrawdownState()
                return

            state = payload.get("drawdown_state")
            if not isinstance(state, dict):
                return

            try:
                self._dd.state = _DrawdownState(
                    peak_equity=float(state.get("peak_equity") or 0.0),
                    last_equity=float(state.get("last_equity") or 0.0),
                    drawdown=float(state.get("drawdown") or 0.0),
                    max_drawdown_seen=float(state.get("max_drawdown_seen") or 0.0),
                    halted=bool(state.get("halted", False)),
                    halt_reason=state.get("halt_reason"),
                    cooldown_until_ts=(
                        float(state["cooldown_until_ts"])
                        if state.get("cooldown_until_ts") is not None else None
                    ),
                    last_day_key=state.get("last_day_key"),
                    day_start_equity=float(state.get("day_start_equity") or 0.0),
                    daily_loss_pct=float(state.get("daily_loss_pct") or 0.0),
                    consecutive_failures=int(state.get("consecutive_failures") or 0),
                )
            except Exception:
                self._dd.state = _DrawdownState()

    def _save_state(self) -> None:
        with self._lock:
            payload = {
                "pair": self.pair,
                "updated_ts": time.time(),
                "config": asdict(self.config),
                "drawdown_state": {
                    "peak_equity": self._dd.state.peak_equity,
                    "last_equity": self._dd.state.last_equity,
                    "drawdown": self._dd.state.drawdown,
                    "max_drawdown_seen": self._dd.state.max_drawdown_seen,
                    "halted": self._dd.state.halted,
                    "halt_reason": self._dd.state.halt_reason,
                    "cooldown_until_ts": self._dd.state.cooldown_until_ts,
                    "last_day_key": self._dd.state.last_day_key,
                    "day_start_equity": self._dd.state.day_start_equity,
                    "daily_loss_pct": self._dd.state.daily_loss_pct,
                    "consecutive_failures": self._dd.state.consecutive_failures,
                },
            }

            tmp_file = self._state_file.with_suffix(f"{self._state_file.suffix}.tmp")
            payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

            with open(tmp_file, "w", encoding="utf-8") as fh:
                fh.write(payload_text)
                fh.flush()
                os.fsync(fh.fileno())

            os.replace(tmp_file, self._state_file)

    # ------------------------------------------------------------------
    # Health / state
    # ------------------------------------------------------------------

    def note_equity(self, equity: float, now_ts: Optional[float] = None) -> None:
        with self._lock:
            self._dd.update(float(equity or 0.0), now_ts=now_ts)
            self._save_state()

    def note_execution_failure(self, now_ts: Optional[float] = None) -> None:
        with self._lock:
            self._dd.register_failure(now_ts=now_ts)
            self._save_state()

    def note_execution_success(self) -> None:
        with self._lock:
            self._dd.clear_failures()
            self._save_state()

    def diagnostics(self) -> Dict[str, Any]:
        with self._lock:
            state = self._dd.state
            risk_pressure = self._risk_pressure_score(
                drawdown=float(state.drawdown),
                daily_loss_pct=float(state.daily_loss_pct),
                consecutive_failures=int(state.consecutive_failures),
                current_total_exposure=0.0,
                equity=float(state.last_equity),
            )
            return {
                "pair": self.pair,
                "peak_equity": float(state.peak_equity),
                "last_equity": float(state.last_equity),
                "drawdown": float(state.drawdown),
                "max_drawdown_seen": float(state.max_drawdown_seen),
                "daily_loss_pct": float(state.daily_loss_pct),
                "halted": bool(state.halted),
                "halt_reason": state.halt_reason,
                "cooldown_until_ts": state.cooldown_until_ts,
                "consecutive_failures": int(state.consecutive_failures),
                "max_drawdown_pct": float(self.config.max_drawdown_pct),
                "max_daily_loss_pct": float(self.config.max_daily_loss_pct),
                "drawdown_cooldown_seconds": float(self.config.drawdown_cooldown_seconds),
                "max_consecutive_failures": int(self.config.max_consecutive_failures),
                "risk_pressure_score": float(risk_pressure["score"]),
                "risk_pressure_components": risk_pressure["components"],
            }

    # ------------------------------------------------------------------
    # Sizing / validation
    # ------------------------------------------------------------------

    def position_size(
        self,
        *,
        equity: float,
        price: float,
        atr: float,
        risk_pct: Optional[float] = None,
        corr_mult: float = 1.0,
        current_total_exposure: float = 0.0,
        quote_balance: Optional[float] = None,
        now_ts: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> float:
        size, _ = self.size_and_diag(
            equity=equity,
            price=price,
            atr=atr,
            risk_pct=risk_pct,
            corr_mult=corr_mult,
            current_total_exposure=current_total_exposure,
            quote_balance=quote_balance,
            now_ts=now_ts,
            stop_loss=stop_loss,
        )
        return float(size)

    def size_and_diag(
        self,
        *,
        equity: float,
        price: float,
        atr: float,
        risk_pct: Optional[float] = None,
        corr_mult: float = 1.0,
        current_total_exposure: float = 0.0,
        quote_balance: Optional[float] = None,
        now_ts: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        with self._lock:
            cfg = self.config

            eq = float(equity or 0.0)
            px = float(price or 0.0)
            atr_q = float(atr or 0.0)
            rp = float(risk_pct if risk_pct is not None else cfg.default_risk_pct)
            cm = float(corr_mult or 1.0)
            exposure = float(current_total_exposure or 0.0)
            qb = float(quote_balance) if quote_balance is not None else None
            sl = float(stop_loss) if stop_loss is not None else 0.0

            diag: Dict[str, Any] = {
                "allowed": False,
                "reason": None,
                "inputs": {
                    "equity": eq,
                    "price": px,
                    "atr": atr_q,
                    "risk_pct": rp,
                    "corr_mult": cm,
                    "current_total_exposure": exposure,
                    "quote_balance": qb,
                    "stop_loss": sl if sl > 0.0 else None,
                },
                "limits": {},
                "drawdown": {},
                "risk_pressure": {},
            }

            if px <= 0.0:
                diag["reason"] = "invalid_price"
                return 0.0, diag

            dd_state = self._dd.update(eq, now_ts=now_ts)
            diag["drawdown"] = {
                "peak_equity": float(dd_state.peak_equity),
                "last_equity": float(dd_state.last_equity),
                "drawdown": float(dd_state.drawdown),
                "max_drawdown_seen": float(dd_state.max_drawdown_seen),
                "daily_loss_pct": float(dd_state.daily_loss_pct),
                "halted": bool(dd_state.halted),
                "halt_reason": dd_state.halt_reason,
                "cooldown_until_ts": dd_state.cooldown_until_ts,
                "max_drawdown_pct": float(cfg.max_drawdown_pct),
                "max_daily_loss_pct": float(cfg.max_daily_loss_pct),
                "consecutive_failures": int(dd_state.consecutive_failures),
                "max_consecutive_failures": int(cfg.max_consecutive_failures),
            }

            risk_pressure = self._risk_pressure_score(
                drawdown=float(dd_state.drawdown),
                daily_loss_pct=float(dd_state.daily_loss_pct),
                consecutive_failures=int(dd_state.consecutive_failures),
                current_total_exposure=exposure,
                equity=eq,
            )
            diag["risk_pressure"] = risk_pressure

            if dd_state.halted:
                diag["reason"] = dd_state.halt_reason or "risk_halt_active"
                self._save_state()
                return 0.0, diag

            if eq <= 0.0:
                diag["reason"] = "equity_missing"
                self._save_state()
                return 0.0, diag

            effective_atr, atr_source = self._effective_atr(price=px, atr=atr_q)
            diag["limits"]["effective_atr"] = float(effective_atr)
            diag["limits"]["effective_atr_source"] = atr_source

            if effective_atr <= 0.0 and cfg.block_if_atr_missing:
                diag["reason"] = "atr_missing"
                self._save_state()
                return 0.0, diag

            max_total_exposure = eq * cfg.max_total_exposure_pct if eq > 0 else 0.0
            diag["limits"]["max_total_exposure"] = float(max_total_exposure)
            diag["limits"]["max_total_exposure_pct"] = float(cfg.max_total_exposure_pct)
            diag["limits"]["current_total_exposure"] = float(exposure)

            if max_total_exposure > 0 and exposure >= max_total_exposure:
                diag["reason"] = "total_exposure_cap_reached"
                self._save_state()
                return 0.0, diag

            risk_budget = eq * max(0.0, rp)
            if cm <= 0:
                cm = 1.0

            stop_distance, stop_source = self._resolve_stop_distance(
                price=px,
                stop_loss=sl,
                effective_atr=effective_atr,
            )

            diag["limits"]["risk_budget"] = float(risk_budget)
            diag["limits"]["stop_distance"] = float(stop_distance)
            diag["limits"]["stop_distance_source"] = stop_source

            if stop_distance <= 0.0:
                diag["reason"] = "invalid_stop_distance"
                self._save_state()
                return 0.0, diag

            raw_size = (risk_budget / stop_distance) / cm

            adaptive_slippage_pct = self._adaptive_slippage_pct(
                price=px,
                effective_atr=effective_atr,
            )
            buffer_pct = max(0.0, cfg.fee_pct + adaptive_slippage_pct)
            raw_size *= max(0.0, (1.0 - buffer_pct))

            diag["limits"]["fee_pct"] = float(cfg.fee_pct)
            diag["limits"]["adaptive_slippage_pct"] = float(adaptive_slippage_pct)
            diag["limits"]["fee_slippage_buffer_pct"] = float(buffer_pct)
            diag["limits"]["raw_size"] = float(raw_size)

            if raw_size <= 0.0:
                diag["reason"] = "size_non_positive"
                self._save_state()
                return 0.0, diag

            notional = raw_size * px
            diag["limits"]["raw_notional"] = float(notional)

            if notional < cfg.min_trade_value:
                diag["reason"] = "min_trade_value"
                diag["limits"]["min_trade_value"] = float(cfg.min_trade_value)
                self._save_state()
                return 0.0, diag

            if cfg.max_trade_value > 0 and notional > cfg.max_trade_value:
                raw_size = cfg.max_trade_value / px
                notional = raw_size * px
                diag["limits"]["clamped_by_max_trade_value"] = True
                diag["limits"]["max_trade_value"] = float(cfg.max_trade_value)

            max_pos_value = eq * cfg.max_position_value_pct
            diag["limits"]["max_position_value"] = float(max_pos_value)
            diag["limits"]["max_position_value_pct"] = float(cfg.max_position_value_pct)
            if max_pos_value > 0 and notional > max_pos_value:
                raw_size = max_pos_value / px
                notional = raw_size * px
                diag["limits"]["clamped_by_max_position_value"] = True

            if max_total_exposure > 0:
                headroom = max(0.0, max_total_exposure - exposure)
                diag["limits"]["exposure_headroom"] = float(headroom)
                if headroom <= 0:
                    diag["reason"] = "total_exposure_cap_reached"
                    self._save_state()
                    return 0.0, diag
                if notional > headroom:
                    raw_size = headroom / px
                    notional = raw_size * px
                    diag["limits"]["clamped_by_exposure_headroom"] = True

            if qb is not None:
                diag["limits"]["quote_balance"] = float(qb)
                if qb <= 0.0:
                    if not cfg.allow_sizing_without_quote_balance:
                        diag["reason"] = "quote_balance_missing"
                        self._save_state()
                        return 0.0, diag
                else:
                    if notional > qb:
                        raw_size = qb / px
                        notional = raw_size * px
                        diag["limits"]["clamped_by_quote_balance"] = True

            if cfg.max_size > 0 and raw_size > cfg.max_size:
                raw_size = cfg.max_size
                notional = raw_size * px
                diag["limits"]["clamped_by_max_size"] = True
                diag["limits"]["max_size"] = float(cfg.max_size)

            if cfg.min_size > 0 and raw_size < cfg.min_size:
                diag["reason"] = "min_size"
                diag["limits"]["min_size"] = float(cfg.min_size)
                self._save_state()
                return 0.0, diag

            if notional < cfg.min_trade_value:
                diag["reason"] = "min_trade_value_after_clamp"
                diag["limits"]["min_trade_value"] = float(cfg.min_trade_value)
                self._save_state()
                return 0.0, diag

            diag["allowed"] = True
            diag["reason"] = "OK"
            diag["limits"]["final_size"] = float(raw_size)
            diag["limits"]["final_notional"] = float(notional)
            diag["atr"] = float(effective_atr)
            diag["drawdown_pct"] = float(dd_state.drawdown)
            diag["daily_loss_pct"] = float(dd_state.daily_loss_pct)
            diag["risk_pressure_score"] = float(risk_pressure["score"])
            diag["risk_pressure_components"] = risk_pressure["components"]

            self._save_state()
            return float(raw_size), diag

    def validate_and_adjust(
        self,
        *,
        decision: Dict[str, Any],
        equity: float,
        quote_balance: Optional[float],
        current_total_exposure: float,
        atr: float,
        corr_mult: Optional[float] = None,
        now_ts: Optional[float] = None,
    ) -> Tuple[bool, Dict[str, Any], str, Dict[str, Any]]:
        with self._lock:
            adjusted = dict(decision or {})
            side = str(adjusted.get("side", "HOLD")).upper().strip()
            intent = str(adjusted.get("intent") or "").lower().strip()
            reduce_only = bool(adjusted.get("reduce_only", False))

            if side == "HOLD":
                diag = self.diagnostics()
                diag["reason"] = "hold_signal"
                return True, adjusted, "hold_signal", diag

            price = float(adjusted.get("price") or 0.0)
            stop_loss = adjusted.get("stop_loss")
            requested_amount = float(adjusted.get("amount") or 0.0)

            # Reduce/exit actions must not be upsized by entry risk logic.
            if reduce_only or intent in {"reduce", "exit"}:
                diag = self.diagnostics()
                diag["allowed"] = requested_amount > 0.0 and price > 0.0
                diag["reason"] = "ok" if diag["allowed"] else "invalid_reduce_order"
                diag["price"] = price
                diag["requested_amount"] = requested_amount
                diag["risk_pressure_score"] = float(diag.get("risk_pressure_score", 0.0))
                diag["risk_pressure_components"] = diag.get("risk_pressure_components", {})
                if not diag["allowed"]:
                    return False, adjusted, str(diag["reason"]), diag
                adjusted["amount"] = requested_amount
                adjusted["risk_pressure_score"] = float(diag.get("risk_pressure_score", 0.0))
                return True, adjusted, "ok", diag

            size, diag = self.size_and_diag(
                equity=float(equity or 0.0),
                price=price,
                atr=float(atr or 0.0),
                risk_pct=None,
                corr_mult=float(corr_mult or 1.0),
                current_total_exposure=float(current_total_exposure or 0.0),
                quote_balance=quote_balance,
                now_ts=now_ts,
                stop_loss=(float(stop_loss) if stop_loss not in (None, "") else None),
            )

            if size <= 0.0:
                return False, adjusted, str(diag.get("reason", "risk_blocked")), diag

            # Never enlarge an explicit requested amount.
            final_amount = float(size)
            if requested_amount > 0.0:
                final_amount = min(final_amount, requested_amount)

            if final_amount <= 0.0:
                diag["reason"] = "final_amount_non_positive"
                return False, adjusted, "final_amount_non_positive", diag

            adjusted["amount"] = float(final_amount)
            adjusted["risk_pressure_score"] = float(diag.get("risk_pressure_score", 0.0))
            adjusted["risk_pressure_components"] = diag.get("risk_pressure_components", {})
            return True, adjusted, "ok", diag

    # ------------------------------------------------------------------
    # Compatibility helpers
    # ------------------------------------------------------------------

    def can_open_position(self, **kwargs: Any) -> bool:
        decision = {
            "side": str(kwargs.get("side") or "BUY").upper(),
            "price": float(kwargs.get("price") or 0.0),
            "amount": float(kwargs.get("amount") or 0.0),
            "intent": "entry",
            "reduce_only": False,
        }
        allowed, _, _, _ = self.validate_and_adjust(
            decision=decision,
            equity=float(kwargs.get("equity") or 0.0),
            quote_balance=kwargs.get("quote_balance"),
            current_total_exposure=float(kwargs.get("current_total_exposure") or 0.0),
            atr=float(kwargs.get("atr") or 0.0),
            corr_mult=float(kwargs.get("corr_mult") or 1.0),
            now_ts=kwargs.get("now_ts"),
        )
        return bool(allowed)

    def can_reduce_position(self, **kwargs: Any) -> bool:
        amount = float(kwargs.get("amount") or 0.0)
        price = float(kwargs.get("price") or 0.0)
        return amount > 0.0 and price > 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _effective_atr(self, *, price: float, atr: float) -> Tuple[float, str]:
        cfg = self.config
        atr_q = float(atr or 0.0)
        px = float(price or 0.0)

        if atr_q > 0.0:
            return atr_q, "atr"

        fallback = max(
            float(cfg.atr_fallback_min_abs),
            max(0.0, px) * max(0.0, cfg.atr_fallback_pct_of_price),
        )

        if fallback > 0.0:
            return fallback, "fallback_from_price"

        return 0.0, "missing"

    def _resolve_stop_distance(
        self,
        *,
        price: float,
        stop_loss: float,
        effective_atr: float,
    ) -> Tuple[float, str]:
        px = float(price or 0.0)
        sl = float(stop_loss or 0.0)
        atr_q = float(effective_atr or 0.0)

        if px > 0.0 and sl > 0.0 and sl != px:
            return abs(px - sl), "stop_loss"

        if atr_q > 0.0:
            return atr_q, "atr"

        default_distance = px * max(0.0, self.config.default_stop_loss_pct)
        if default_distance > 0.0:
            return default_distance, "default_stop_loss_pct"

        return 0.0, "missing"

    def _adaptive_slippage_pct(self, *, price: float, effective_atr: float) -> float:
        cfg = self.config
        px = float(price or 0.0)
        atr_q = float(effective_atr or 0.0)

        base = max(0.0, cfg.slippage_pct)
        if px <= 0.0 or atr_q <= 0.0:
            return base

        volatility_slippage = (atr_q / px) * max(0.0, cfg.adaptive_slippage_atr_mult)
        capped_volatility_slippage = min(
            max(0.0, cfg.adaptive_slippage_max_pct),
            max(0.0, volatility_slippage),
        )

        return max(base, capped_volatility_slippage)

    def _risk_pressure_score(
        self,
        *,
        drawdown: float,
        daily_loss_pct: float,
        consecutive_failures: int,
        current_total_exposure: float,
        equity: float,
    ) -> Dict[str, Any]:
        cfg = self.config

        dd_pressure = 0.0
        if cfg.max_drawdown_pct > 0:
            dd_pressure = min(1.0, max(0.0, drawdown / cfg.max_drawdown_pct))

        daily_pressure = 0.0
        if cfg.max_daily_loss_pct > 0:
            daily_pressure = min(1.0, max(0.0, daily_loss_pct / cfg.max_daily_loss_pct))

        exposure_pressure = 0.0
        if equity > 0 and cfg.max_total_exposure_pct > 0:
            exposure_cap = equity * cfg.max_total_exposure_pct
            if exposure_cap > 0:
                exposure_pressure = min(1.0, max(0.0, current_total_exposure / exposure_cap))

        failure_pressure = 0.0
        if cfg.max_consecutive_failures > 0:
            failure_pressure = min(1.0, max(0.0, consecutive_failures / cfg.max_consecutive_failures))

        weighted = (
            dd_pressure * max(0.0, cfg.risk_pressure_drawdown_weight)
            + daily_pressure * max(0.0, cfg.risk_pressure_daily_loss_weight)
            + exposure_pressure * max(0.0, cfg.risk_pressure_exposure_weight)
            + failure_pressure * max(0.0, cfg.risk_pressure_failures_weight)
        )

        total_weight = (
            max(0.0, cfg.risk_pressure_drawdown_weight)
            + max(0.0, cfg.risk_pressure_daily_loss_weight)
            + max(0.0, cfg.risk_pressure_exposure_weight)
            + max(0.0, cfg.risk_pressure_failures_weight)
        )

        score = (weighted / total_weight) if total_weight > 0 else 0.0
        score = min(1.0, max(0.0, score))

        return {
            "score": float(score),
            "components": {
                "drawdown_pressure": float(dd_pressure),
                "daily_loss_pressure": float(daily_pressure),
                "exposure_pressure": float(exposure_pressure),
                "failure_pressure": float(failure_pressure),
            },
        }