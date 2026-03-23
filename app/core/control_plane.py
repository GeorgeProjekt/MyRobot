from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Optional, Dict, Any

from app.storage import db


@dataclass(frozen=True)
class ControlState:
    pause_new_trades: bool
    kill_switch: bool
    reduce_only: bool
    mode: str
    armed: bool
    reason: Optional[str]
    live_readiness: bool = False
    last_readiness_check: Optional[str] = None
    readiness: Dict[str, Any] = field(default_factory=dict)


class ControlPlane:
    """
    DB-backed control plane with:
    - global controls
    - optional pair-scoped overrides

    Compatibility:
    - existing callers without `pair` continue to use global scope
    - callers may pass `pair="BTC_EUR"` to get isolated per-pair controls

    Rules:
    - global kill_switch = hard global block
    - pair kill_switch = hard block for that pair only
    - pause_new_trades blocks new entries
    - reduce_only allows only exposure reduction
    - mode = paper/live
    - armed = live execution allowed only when mode == live and readiness is true
    """

    K_PAUSE = "control_pause_new_trades"
    K_KILL = "control_kill_switch"
    K_REDUCE = "control_reduce_only"
    K_MODE = "control_mode"
    K_ARMED = "control_armed"
    K_REASON = "control_reason"
    K_LIVE_READINESS = "control_live_readiness"
    K_LAST_READINESS_CHECK = "control_last_readiness_check"
    K_READINESS = "control_readiness"

    def __init__(self) -> None:
        self._lock = RLock()

    # ---------------------------------------------------------
    # STATE
    # ---------------------------------------------------------

    def get(self, pair: str | None = None) -> ControlState:
        with self._lock:
            normalized_pair = self._normalize_pair(pair)

            global_mode = self._normalize_mode(db.kv_get(self.K_MODE, "paper"))
            global_kill_switch = self._get_bool(self.K_KILL, False)
            global_pause_new_trades = self._get_bool(self.K_PAUSE, False)
            global_reduce_only = self._get_bool(self.K_REDUCE, False)
            global_armed = self._get_bool(self.K_ARMED, False)
            global_live_readiness = self._get_bool(self.K_LIVE_READINESS, False)
            global_last_readiness_check = db.kv_get(self.K_LAST_READINESS_CHECK, "") or None
            global_readiness = self._get_json(self.K_READINESS, {})
            global_reason = db.kv_get(self.K_REASON, "") or None

            if global_mode != "live":
                global_armed = False

            if global_kill_switch:
                global_pause_new_trades = True
                global_armed = False

            if global_mode == "live" and not global_live_readiness:
                global_armed = False

            if normalized_pair is None:
                return ControlState(
                    pause_new_trades=global_pause_new_trades,
                    kill_switch=global_kill_switch,
                    reduce_only=global_reduce_only,
                    mode=global_mode,
                    armed=global_armed,
                    reason=global_reason,
                    live_readiness=global_live_readiness,
                    last_readiness_check=global_last_readiness_check,
                    readiness=global_readiness,
                )

            pair_mode = self._normalize_mode(
                db.kv_get(self._key(self.K_MODE, normalized_pair), global_mode)
            )
            pair_kill_switch = self._get_bool(self._key(self.K_KILL, normalized_pair), False)
            pair_pause_new_trades = self._get_bool(
                self._key(self.K_PAUSE, normalized_pair),
                False,
            )
            pair_reduce_only = self._get_bool(
                self._key(self.K_REDUCE, normalized_pair),
                False,
            )
            pair_armed = self._get_bool(
                self._key(self.K_ARMED, normalized_pair),
                global_armed,
            )
            pair_live_readiness = self._get_bool(
                self._key(self.K_LIVE_READINESS, normalized_pair),
                global_live_readiness,
            )
            pair_last_readiness_check = (
                db.kv_get(self._key(self.K_LAST_READINESS_CHECK, normalized_pair), "") or global_last_readiness_check
            )
            pair_readiness = self._get_json(
                self._key(self.K_READINESS, normalized_pair),
                global_readiness,
            )
            pair_reason = db.kv_get(self._key(self.K_REASON, normalized_pair), "") or global_reason

            effective_kill_switch = global_kill_switch or pair_kill_switch
            effective_pause_new_trades = global_pause_new_trades or pair_pause_new_trades or effective_kill_switch
            effective_reduce_only = global_reduce_only or pair_reduce_only
            effective_mode = pair_mode if pair_mode else global_mode
            effective_live_readiness = bool(pair_live_readiness)
            effective_armed = bool(pair_armed)

            if effective_mode != "live":
                effective_armed = False

            if effective_kill_switch:
                effective_armed = False

            if effective_mode == "live" and not effective_live_readiness:
                effective_armed = False

            return ControlState(
                pause_new_trades=effective_pause_new_trades,
                kill_switch=effective_kill_switch,
                reduce_only=effective_reduce_only,
                mode=effective_mode,
                armed=effective_armed,
                reason=pair_reason,
                live_readiness=effective_live_readiness,
                last_readiness_check=pair_last_readiness_check,
                readiness=pair_readiness,
            )

    # ---------------------------------------------------------
    # CONTROL ACTIONS
    # ---------------------------------------------------------

    def set_pause(
        self,
        enabled: bool,
        reason: str | None = None,
        pair: str | None = None,
    ) -> ControlState:
        with self._lock:
            key = self._scoped_key(self.K_PAUSE, pair)
            db.kv_set(key, self._bool_str(enabled))
            self._set_reason(reason, pair=pair)
            return self.get(pair=pair)

    def set_kill(
        self,
        enabled: bool,
        reason: str | None = None,
        pair: str | None = None,
    ) -> ControlState:
        with self._lock:
            normalized_pair = self._normalize_pair(pair)
            kill_key = self._scoped_key(self.K_KILL, normalized_pair)
            db.kv_set(kill_key, self._bool_str(enabled))

            if enabled:
                db.kv_set(self._scoped_key(self.K_PAUSE, normalized_pair), "1")
                db.kv_set(self._scoped_key(self.K_ARMED, normalized_pair), "0")

                # preserve backward-compatible global behavior
                if normalized_pair is None:
                    db.kv_set(self.K_MODE, "paper")
                else:
                    db.kv_set(self._key(self.K_MODE, normalized_pair), "paper")

            self._set_reason(reason, pair=normalized_pair)
            return self.get(pair=normalized_pair)

    def set_emergency_stop(
        self,
        enabled: bool,
        reason: str | None = None,
        pair: str | None = None,
    ) -> ControlState:
        return self.set_kill(enabled, reason=reason, pair=pair)

    def set_reduce_only(
        self,
        enabled: bool,
        reason: str | None = None,
        pair: str | None = None,
    ) -> ControlState:
        with self._lock:
            db.kv_set(self._scoped_key(self.K_REDUCE, pair), self._bool_str(enabled))
            self._set_reason(reason, pair=pair)
            return self.get(pair=pair)

    def set_mode(
        self,
        mode: str,
        reason: str | None = None,
        pair: str | None = None,
    ) -> ControlState:
        with self._lock:
            normalized = self._normalize_mode(mode)
            normalized_pair = self._normalize_pair(pair)

            if normalized == "live":
                if normalized_pair is None:
                    live_ready = self._get_bool(self.K_LIVE_READINESS, False)
                else:
                    live_ready = self.get(pair=normalized_pair).live_readiness

                if not live_ready:
                    raise RuntimeError("Cannot switch to live mode: live_readiness = False")

            db.kv_set(self._scoped_key(self.K_MODE, normalized_pair), normalized)

            if normalized != "live":
                db.kv_set(self._scoped_key(self.K_ARMED, normalized_pair), "0")

            if normalized_pair is None:
                if normalized == "live" and self._get_bool(self.K_KILL, False):
                    db.kv_set(self.K_MODE, "paper")
            else:
                effective_global_kill = self._get_bool(self.K_KILL, False)
                if normalized == "live" and effective_global_kill:
                    db.kv_set(self._key(self.K_MODE, normalized_pair), "paper")

            self._set_reason(reason, pair=normalized_pair)
            return self.get(pair=normalized_pair)

    def set_armed(
        self,
        enabled: bool,
        reason: str | None = None,
        pair: str | None = None,
    ) -> ControlState:
        with self._lock:
            current = self.get(pair=pair)

            if enabled:
                if current.kill_switch:
                    raise RuntimeError("Cannot arm while kill switch is active")

                if current.mode != "live":
                    raise RuntimeError("Cannot arm unless mode is live")

                if not current.live_readiness:
                    raise RuntimeError("Cannot arm: live readiness check failed")

            db.kv_set(self._scoped_key(self.K_ARMED, pair), self._bool_str(enabled))
            self._set_reason(reason, pair=pair)
            return self.get(pair=pair)

    def set_readiness(
        self,
        live_readiness: bool,
        readiness: Dict[str, Any] | None = None,
        checked_at: str | None = None,
        pair: str | None = None,
    ) -> ControlState:
        with self._lock:
            db.kv_set(self._scoped_key(self.K_LIVE_READINESS, pair), self._bool_str(live_readiness))
            db.kv_set(self._scoped_key(self.K_LAST_READINESS_CHECK, pair), checked_at or "")
            db.kv_set(self._scoped_key(self.K_READINESS, pair), self._json_str(readiness or {}))
            return self.get(pair=pair)

    # ---------------------------------------------------------
    # RUNTIME GUARDS
    # ---------------------------------------------------------

    def can_trade(self, pair: str | None = None) -> bool:
        state = self.get(pair=pair)

        if state.kill_switch:
            return False

        if state.mode == "live" and not state.armed:
            return False

        return True

    def can_open_position(self, pair: str | None = None) -> bool:
        state = self.get(pair=pair)

        if not self.can_trade(pair=pair):
            return False

        if state.pause_new_trades:
            return False

        if state.reduce_only:
            return False

        return True

    def can_reduce_position(self, pair: str | None = None) -> bool:
        state = self.get(pair=pair)

        if not self.can_trade(pair=pair):
            return False

        if state.kill_switch:
            return False

        return True

    # ---------------------------------------------------------
    # RESET
    # ---------------------------------------------------------

    def clear_reason(self, pair: str | None = None) -> None:
        db.kv_set(self._scoped_key(self.K_REASON, pair), "")

    def sync_pair_runtime_to_global(
        self,
        pair: str,
        reason: str | None = None,
    ) -> ControlState:
        with self._lock:
            normalized_pair = self._normalize_pair(pair)
            if normalized_pair is None:
                return self.get(pair=None)

            global_state = self.get(pair=None)
            db.kv_set(self._key(self.K_MODE, normalized_pair), self._normalize_mode(global_state.mode))
            db.kv_set(self._key(self.K_ARMED, normalized_pair), self._bool_str(bool(global_state.armed)))
            self._set_reason(reason, pair=normalized_pair)
            return self.get(pair=normalized_pair)

    def reset_safe_defaults(
        self,
        reason: str | None = None,
        pair: str | None = None,
    ) -> ControlState:
        with self._lock:
            normalized_pair = self._normalize_pair(pair)

            db.kv_set(self._scoped_key(self.K_PAUSE, normalized_pair), "0")
            db.kv_set(self._scoped_key(self.K_KILL, normalized_pair), "0")
            db.kv_set(self._scoped_key(self.K_REDUCE, normalized_pair), "0")
            db.kv_set(self._scoped_key(self.K_MODE, normalized_pair), "paper")
            db.kv_set(self._scoped_key(self.K_ARMED, normalized_pair), "0")
            db.kv_set(self._scoped_key(self.K_LIVE_READINESS, normalized_pair), "0")
            db.kv_set(self._scoped_key(self.K_LAST_READINESS_CHECK, normalized_pair), "")
            db.kv_set(self._scoped_key(self.K_READINESS, normalized_pair), "{}")

            self._set_reason(reason, pair=normalized_pair)
            return self.get(pair=normalized_pair)

    def reset_runtime_guards(
        self,
        reason: str | None = None,
        pair: str | None = None,
    ) -> ControlState:
        with self._lock:
            normalized_pair = self._normalize_pair(pair)

            db.kv_set(self._scoped_key(self.K_PAUSE, normalized_pair), "0")
            db.kv_set(self._scoped_key(self.K_KILL, normalized_pair), "0")
            db.kv_set(self._scoped_key(self.K_REDUCE, normalized_pair), "0")

            if normalized_pair is None:
                db.kv_set(self.K_ARMED, "0")
                db.kv_set(self.K_MODE, "paper")
            else:
                global_state = self.get(pair=None)
                db.kv_set(self._key(self.K_ARMED, normalized_pair), self._bool_str(bool(global_state.armed)))
                db.kv_set(self._key(self.K_MODE, normalized_pair), self._normalize_mode(global_state.mode))

            self._set_reason(reason, pair=normalized_pair)
            return self.get(pair=normalized_pair)

    # ---------------------------------------------------------
    # SERIALIZATION
    # ---------------------------------------------------------

    def as_dict(self, pair: str | None = None) -> Dict[str, Any]:
        state = self.get(pair=pair)

        payload = {
            "pause_new_trades": state.pause_new_trades,
            "kill_switch": state.kill_switch,
            "emergency_stop": state.kill_switch,
            "reduce_only": state.reduce_only,
            "mode": state.mode,
            "armed": state.armed,
            "reason": state.reason,
            "live_readiness": state.live_readiness,
            "last_readiness_check": state.last_readiness_check,
            "readiness": state.readiness,
        }

        if pair is not None:
            payload["pair"] = self._normalize_pair(pair)

        return payload

    # ---------------------------------------------------------
    # INTERNAL
    # ---------------------------------------------------------

    def _set_reason(self, reason: str | None, pair: str | None = None) -> None:
        if reason is not None:
            db.kv_set(self._scoped_key(self.K_REASON, pair), str(reason))

    def _get_bool(self, key: str, default: bool) -> bool:
        raw = db.kv_get(key, "1" if default else "0")
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _bool_str(self, value: bool) -> str:
        return "1" if bool(value) else "0"

    def _normalize_mode(self, mode: Any) -> str:
        return "live" if str(mode or "").strip().lower() == "live" else "paper"

    def _normalize_pair(self, pair: Any) -> Optional[str]:
        raw = str(pair or "").strip().upper()
        return raw or None

    def _key(self, base: str, pair: str) -> str:
        return f"{base}:{pair}"

    def _scoped_key(self, base: str, pair: str | None) -> str:
        normalized_pair = self._normalize_pair(pair)
        if normalized_pair is None:
            return base
        return self._key(base, normalized_pair)

    def _json_str(self, value: Dict[str, Any]) -> str:
        try:
            import json
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return "{}"

    def _get_json(self, key: str, default: Dict[str, Any]) -> Dict[str, Any]:
        raw = db.kv_get(key, "")

        if raw in (None, ""):
            return dict(default)

        try:
            import json
            parsed = json.loads(str(raw))
            return parsed if isinstance(parsed, dict) else dict(default)
        except Exception:
            return dict(default)