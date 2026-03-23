from __future__ import annotations

import asyncio
import inspect
import logging
import os
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

try:
    from app.core.metrics import inc_error, set_gauge
except Exception:
    def inc_error(*args, **kwargs):
        return None

    def set_gauge(*args, **kwargs):
        return None

from app.models.robot_state import RobotState
from app.core.control_plane import ControlPlane
from app.runtime.watchdog_state import record_pair_health

try:
    from app.core.telemetry import telemetry_hub
except Exception:
    class _NullTelemetryHub:
        def emit(self, *args, **kwargs):
            return None

        def publish(self, *args, **kwargs):
            return None

        def push(self, *args, **kwargs):
            return None

    telemetry_hub = _NullTelemetryHub()

from app.services.robot_service import RobotService
from app.core.snapshots.builders import GlobalDashboardSnapshotBuilder


logger = logging.getLogger(__name__)


def _normalize_pair(pair: str) -> str:
    return str(pair or "").upper().strip()


def _parse_pairs_from_env() -> List[str]:
    raw = (os.getenv("ROBOT_PAIRS") or os.getenv("TRADING_PAIRS") or "").strip()
    if not raw:
        return []
    pairs: List[str] = []
    for item in raw.split(","):
        pair = _normalize_pair(item)
        if pair and pair not in pairs:
            pairs.append(pair)
    return pairs


class _FallbackSettings:
    @property
    def trading_pairs(self) -> List[str]:
        env_pairs = _parse_pairs_from_env()
        if env_pairs:
            return env_pairs
        return ["BTC_EUR", "BTC_CZK", "ETH_EUR", "ETH_CZK", "ADA_CZK"]

    @property
    def robot_cycle_interval_sec(self) -> float:
        raw = os.getenv("ROBOT_CYCLE_INTERVAL_SEC") or os.getenv("ROBOT_INTERVAL_SEC") or "2.0"
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 2.0

    @property
    def robot_cycle_timeout_sec(self) -> float:
        raw = os.getenv("ROBOT_CYCLE_TIMEOUT_SEC") or "10.0"
        try:
            return max(1.0, float(raw))
        except (TypeError, ValueError):
            return 10.0

    @property
    def robot_stale_after_sec(self) -> float:
        raw = os.getenv("ROBOT_STALE_AFTER_SEC") or "60.0"
        try:
            return max(5.0, float(raw))
        except (TypeError, ValueError):
            return 60.0

    @property
    def robot_max_trades_in_memory(self) -> int:
        raw = os.getenv("ROBOT_MAX_TRADES_IN_MEMORY") or "2000"
        try:
            return max(100, int(raw))
        except (TypeError, ValueError):
            return 2000

    @property
    def robot_event_log_maxlen(self) -> int:
        raw = os.getenv("ROBOT_EVENT_LOG_MAXLEN") or "5000"
        try:
            return max(500, int(raw))
        except (TypeError, ValueError):
            return 5000

    @property
    def robot_max_restart_backoff_sec(self) -> float:
        raw = os.getenv("ROBOT_MAX_RESTART_BACKOFF_SEC") or "60.0"
        try:
            return max(5.0, float(raw))
        except (TypeError, ValueError):
            return 60.0


settings = _FallbackSettings()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GlobalOrchestrator:
    """
    Robust global orchestrator that keeps one long-lived RobotState per pair and
    updates it incrementally instead of replacing it on every cycle.

    Design goals:
    - preserve trade history and last_trade across cycles
    - avoid fake / reset metrics caused by state replacement
    - keep API/dashboard snapshots stable
    - integrate manual trades into orchestrator audit trail
    - keep each pair fully isolated (own service / own account / own capital)
    - support long-running 24/7 self-healing operation
    """

    def __init__(self, runtime_context: Optional[Any] = None) -> None:
        self._services: Dict[str, RobotService] = {}
        self._states: Dict[str, RobotState] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._running = False
        self._started_at = time.time()
        self._control_plane = ControlPlane()
        self._runtime_context = runtime_context
        self._runtime_mode = str(self._control_plane.get().mode or "paper").lower()
        self._last_errors: Dict[str, str] = {}
        self._event_log: deque[Dict[str, Any]] = deque(maxlen=settings.robot_event_log_maxlen)
        self._thread_loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        self._pair_error_count: Dict[str, int] = {}
        self._pair_restart_backoff: Dict[str, float] = {}
        self._pair_last_ok_ts: Dict[str, float] = {}
        self._pair_last_cycle_started_ts: Dict[str, float] = {}
        self._pair_last_cycle_finished_ts: Dict[str, float] = {}
        self._pair_bound_service_ids: Dict[str, int] = {}
        self._service_to_pair: Dict[int, str] = {}
        self._pair_last_status: Dict[str, str] = {}

        self._warned_missing_components: Dict[str, Set[str]] = {}
        self._warned_router_pair_mismatch: Set[str] = set()

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        for pair in list(self._services.keys()):
            await self.start_pair(pair)

        logger.info("GlobalOrchestrator started for %d pairs", len(self._services))

    async def stop(self) -> None:
        self._running = False

        for pair in list(self._tasks.keys()):
            await self.stop_pair(pair)

        logger.info("GlobalOrchestrator stopped")

    def start_in_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            self._thread_loop = loop
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.start())
            try:
                loop.run_forever()
            finally:
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

        self._thread = threading.Thread(target=_runner, daemon=True, name="global-orchestrator")
        self._thread.start()

    def stop_background(self) -> None:
        if self._thread_loop and self._thread_loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self.stop(), self._thread_loop)
            try:
                fut.result(timeout=10)
            except Exception:
                pass
            try:
                self._thread_loop.call_soon_threadsafe(self._thread_loop.stop)
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._running

    def _is_pair_task_active(self, pair: str) -> bool:
        pair = _normalize_pair(pair)
        task = self._tasks.get(pair)
        return bool(task is not None and not task.done())

    def _normalize_status_name(self, value: Any) -> str:
        raw = getattr(value, "value", value)
        name = str(raw or "").strip().lower()
        aliases = {
            "emergency_stop": "stopped",
            "stop": "stopped",
            "halted": "stopped",
            "halt": "stopped",
            "cancelled": "stopped",
            "canceled": "stopped",
            "idle": "stopped",
            "ok": "running",
            "healthy": "running",
            "started": "running",
            "active": "running",
            "ready": "running",
            "warning": "degraded",
        }
        return aliases.get(name, name)

    def _effective_pair_status(self, pair: str, state: Optional[RobotState] = None) -> str:
        pair = _normalize_pair(pair)
        state = state or self._states.get(pair)
        raw = self._normalize_status_name(self._get_attr(state, "status", None))
        control = self._control_state(pair)

        if bool(getattr(control, "kill_switch", False) or getattr(control, "emergency_stop", False)):
            return "STOPPED"

        if not self._running or not self._is_pair_task_active(pair):
            if raw == "error":
                return "ERROR"
            return "STOPPED"

        if raw == "error":
            return "ERROR"
        if raw in {"degraded", "stale"}:
            return "DEGRADED"
        if raw == "paused":
            return "PAUSED"
        if raw == "starting":
            return "STARTING"
        if raw == "stopped":
            return "STOPPED"
        return "RUNNING"

    def _sanitize_restored_state(self, pair: str, candidate: RobotState) -> RobotState:
        pair = _normalize_pair(pair)
        raw = self._normalize_status_name(self._get_attr(candidate, "status", None))
        if raw in {"running", "starting", "paused", "degraded", "stale"} and not self._is_pair_task_active(pair):
            self._safe_set_attr(candidate, "status", "stopped")
        if bool(getattr(self._control_state(pair), "kill_switch", False) or getattr(self._control_state(pair), "emergency_stop", False)):
            self._safe_set_attr(candidate, "status", "stopped")
        return candidate

    def _control_state(self, pair: Optional[str] = None):
        return self._control_plane.get(pair=pair)

    def _restore_persistent_pair_runtime(self, pair: str) -> None:
        if self._runtime_context is None:
            return
        try:
            payload = self._runtime_context.get_pair_runtime_data(pair)
        except Exception:
            return

        state_payload = payload.get("state")
        candidate = self._coerce_state_candidate(state_payload)
        if candidate is not None:
            candidate = self._sanitize_restored_state(pair, candidate)
            self._update_state_incremental(pair, candidate)

    def _persist_pair_runtime_state(self, pair: str) -> None:
        if self._runtime_context is None:
            return

        pair = _normalize_pair(pair)
        state = self._states.get(pair)
        service = self._services.get(pair)

        payload = {
            "state": self._state_to_dict(state) if state is not None else {},
            "service": {},
            "control": self._control_plane.as_dict(pair=pair),
            "updated_at": _utc_now_iso(),
        }

        if service is not None:
            snapshot_accessor = getattr(service, "get_runtime_snapshot", None)
            if callable(snapshot_accessor):
                try:
                    snapshot = snapshot_accessor()
                    if isinstance(snapshot, dict):
                        payload["service"] = deepcopy(snapshot)
                except Exception:
                    pass

        try:
            self._runtime_context.persist_pair_runtime_data(pair, payload)
        except Exception as exc:
            logger.warning("Failed to persist runtime state for %s: %s", pair, exc)

    def attach_service(self, pair: str, service: RobotService) -> None:
        pair = _normalize_pair(pair)

        existing = self._services.get(pair)
        if existing is service:
            return

        if existing is not None:
            return

        if self._runtime_context is not None:
            try:
                binding = getattr(self._runtime_context, "pair_bindings", {}).get(pair)
                bound_service = getattr(binding, "service", None) if binding is not None else None
                if bound_service is not None:
                    service = bound_service
            except Exception:
                pass

        existing = self._services.get(pair)
        if existing is service:
            return
        if existing is not None:
            return

        self._validate_service_attachment(pair, service)

        self._services[pair] = service
        self._pair_bound_service_ids[pair] = id(service)
        self._service_to_pair[id(service)] = pair
        self._locks.setdefault(pair, asyncio.Lock())

        try:
            setattr(service, "control_plane", self._control_plane)
            setattr(service, "control_pair", pair)
        except Exception:
            pass

        state = self._ensure_state(pair)
        self._restore_persistent_pair_runtime(pair)
        self._set_pair_runtime_metadata(pair, state, service)
        self._append_event(pair, "service_attached", service_id=id(service))
        self._persist_pair_runtime_state(pair)

    def get_service(self, pair: str) -> Optional[RobotService]:
        return self._services.get(_normalize_pair(pair))

    async def start_pair(self, pair: str) -> None:
        pair = _normalize_pair(pair)

        existing = self._tasks.get(pair)
        if existing and not existing.done():
            return

        if existing and existing.done():
            self._tasks.pop(pair, None)

        service = self._services.get(pair)
        if service is None:
            logger.warning("Cannot start pair %s without attached RobotService", pair)
            return

        self._assert_service_pair_integrity(pair, service)
        self._locks.setdefault(pair, asyncio.Lock())

        state = self._ensure_state(pair)
        self._restore_persistent_pair_runtime(pair)
        self._safe_set_attr(state, "status", "starting")
        self._safe_set_attr(state, "last_error", None)

        service_start = getattr(service, "start", None)
        if callable(service_start):
            start_result = service_start()
            if inspect.isawaitable(start_result):
                await start_result

        self._set_pair_runtime_metadata(pair, state, service)

        self._tasks[pair] = asyncio.create_task(self._run_pair_cycle(pair), name=f"pair-cycle-{pair}")
        self._append_event(pair, "pair_started", mode=self._control_state(pair).mode)
        self._persist_pair_runtime_state(pair)

    async def stop_pair(self, pair: str) -> None:
        pair = _normalize_pair(pair)

        task = self._tasks.get(pair)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("Stopping pair task %s ended with error: %s", pair, e)

        self._tasks.pop(pair, None)

        service = self._services.get(pair)
        service_stop = getattr(service, "stop", None) if service is not None else None
        if callable(service_stop):
            try:
                stop_result = service_stop()
                if inspect.isawaitable(stop_result):
                    await stop_result
            except Exception as e:
                logger.warning("Stopping RobotService for %s ended with error: %s", pair, e)

        state = self._ensure_state(pair)
        self._safe_set_attr(state, "status", "stopped")
        self._safe_set_attr(state, "last_cycle_finished_at", _utc_now_iso())
        self._set_pair_runtime_metadata(pair, state, self._services.get(pair))
        self._pair_last_status[pair] = "stopped"
        self._append_event(pair, "pair_stopped")
        self._persist_pair_runtime_state(pair)

    def kill_pair_sync(self, pair: str) -> None:
        pair = _normalize_pair(pair)

        state = self._ensure_state(pair)
        self._safe_set_attr(state, "status", "stopped")
        self._set_pair_runtime_metadata(pair, state, self._services.get(pair))
        self._append_event(pair, "pair_killed")
        self._persist_pair_runtime_state(pair)

        if self._thread_loop and self._thread_loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self.stop_pair(pair), self._thread_loop)
            try:
                fut.result(timeout=10)
            except Exception as e:
                logger.warning("kill_pair_sync failed for %s: %s", pair, e)

    def resume_pair_sync(self, pair: str) -> None:
        pair = _normalize_pair(pair)
        if self._thread_loop and self._thread_loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self.start_pair(pair), self._thread_loop)
            try:
                fut.result(timeout=10)
            except Exception as e:
                logger.warning("resume_pair_sync failed for %s: %s", pair, e)

    async def kill_pair(self, pair: str) -> None:
        await self.stop_pair(pair)

    async def resume_pair(self, pair: str) -> None:
        await self.start_pair(pair)

    def set_trading_mode(self, mode: str) -> None:
        mode = str(mode or "paper").lower().strip()
        self._runtime_mode = mode
        for pair, service in self._services.items():
            try:
                if hasattr(service, "set_trading_mode"):
                    service.set_trading_mode(mode)
                elif hasattr(service, "trading_mode"):
                    setattr(service, "trading_mode", mode)
                state = self._states.get(pair)
                if state is not None:
                    self._set_pair_runtime_metadata(pair, state, service)
            except Exception as e:
                logger.warning("Failed to set trading mode on service %s: %s", pair, e)

    def _new_robot_state(self, pair: str) -> RobotState:
        for kwargs in (
            {"pair_name": pair},
            {"symbol": pair},
            {},
        ):
            try:
                state = RobotState(**kwargs)
                if not kwargs:
                    self._safe_set_attr(state, "pair_name", pair)
                    self._safe_set_attr(state, "symbol", pair)
                return state
            except Exception:
                continue

        state = RobotState()  # type: ignore[call-arg]
        self._safe_set_attr(state, "pair_name", pair)
        self._safe_set_attr(state, "symbol", pair)
        return state

    def _ensure_state(self, pair: str) -> RobotState:
        pair = _normalize_pair(pair)
        state = self._states.get(pair)
        if state is not None:
            return state

        state = self._new_robot_state(pair)
        self._safe_set_attr(state, "status", "starting")
        self._safe_set_attr(state, "trades", list(getattr(state, "trades", []) or []))
        self._safe_set_attr(state, "metadata", dict(getattr(state, "metadata", {}) or {}))
        self._safe_set_attr(state, "extra", dict(getattr(state, "extra", {}) or {}))
        self._states[pair] = state
        self._restore_persistent_pair_runtime(pair)
        self._set_pair_runtime_metadata(pair, state, self._services.get(pair))
        self._persist_pair_runtime_state(pair)
        return state

    def get_pair_state(self, pair: str) -> Optional[RobotState]:
        return self._states.get(_normalize_pair(pair))

    def get_all_states(self) -> Dict[str, RobotState]:
        return dict(self._states)

    def snapshot(self) -> Dict[str, Any]:
        return {
            pair: self._state_to_dict(state)
            for pair, state in self._states.items()
        }

    def _safe_set_attr(self, obj: Any, key: str, value: Any) -> None:
        try:
            setattr(obj, key, value)
        except Exception:
            if hasattr(obj, "__dict__"):
                obj.__dict__[key] = value

    def _get_attr(self, obj: Any, key: str, default: Any = None) -> Any:
        try:
            return getattr(obj, key, default)
        except Exception:
            return default

    def _append_event(self, pair: str, event: str, **payload: Any) -> None:
        self._event_log.append(
            {
                "ts": _utc_now_iso(),
                "pair": pair,
                "event": event,
                **payload,
            }
        )

    def get_event_log(self) -> List[Dict[str, Any]]:
        return list(self._event_log)

    def _state_to_dict(self, state: RobotState) -> Dict[str, Any]:
        try:
            return asdict(state)
        except Exception:
            result: Dict[str, Any] = {}
            if hasattr(state, "__dict__"):
                for key, value in vars(state).items():
                    result[key] = self._to_jsonable(value)
            return result

    def _to_jsonable(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): self._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, deque)):
            return [self._to_jsonable(v) for v in value]
        if hasattr(value, "__dict__"):
            return {str(k): self._to_jsonable(v) for k, v in vars(value).items()}
        return str(value)

    def _merge_dict(self, base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base or {})
        for key, value in (incoming or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_dict(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged

    def _copy_public_attrs(self, source: Any) -> Dict[str, Any]:
        if source is None:
            return {}
        if hasattr(source, "__dict__"):
            return {k: deepcopy(v) for k, v in vars(source).items() if not str(k).startswith("_")}
        if isinstance(source, dict):
            return {k: deepcopy(v) for k, v in source.items()}
        return {}

    def _coerce_state_candidate(self, result: Any) -> Optional[RobotState]:
        if result is None:
            return None

        if isinstance(result, RobotState):
            return result

        if isinstance(result, dict):
            try:
                pair_name = str(result.get("pair_name") or result.get("pair") or result.get("symbol") or "UNKNOWN")
                state = self._new_robot_state(pair_name)
                for key, value in result.items():
                    self._safe_set_attr(state, key, deepcopy(value))
                return state
            except Exception:
                return None

        if hasattr(result, "__dict__"):
            pair_name = str(
                self._get_attr(
                    result,
                    "pair_name",
                    self._get_attr(result, "pair", self._get_attr(result, "symbol", "UNKNOWN")),
                )
            )
            try:
                state = self._new_robot_state(pair_name)
                for key, value in vars(result).items():
                    if str(key).startswith("_"):
                        continue
                    self._safe_set_attr(state, key, deepcopy(value))
                return state
            except Exception:
                return None

        return None

    def _merge_trade_history(self, current: List[Any], incoming: List[Any]) -> List[Any]:
        current = list(current or [])
        incoming = list(incoming or [])
        if not incoming:
            return current

        merged = list(current)
        seen = set()

        def _trade_key(trade: Any) -> str:
            if hasattr(trade, "__dict__"):
                data = vars(trade)
            elif isinstance(trade, dict):
                data = trade
            else:
                return repr(trade)
            return repr(
                (
                    data.get("timestamp"),
                    data.get("side"),
                    data.get("price"),
                    data.get("amount"),
                    data.get("pnl"),
                    data.get("order_id"),
                )
            )

        for trade in current:
            seen.add(_trade_key(trade))

        for trade in incoming:
            key = _trade_key(trade)
            if key not in seen:
                merged.append(trade)
                seen.add(key)

        max_trades = settings.robot_max_trades_in_memory
        if len(merged) > max_trades:
            merged = merged[-max_trades:]

        return merged

    def _update_state_incremental(self, pair: str, candidate: Optional[RobotState]) -> RobotState:
        state = self._ensure_state(pair)
        if candidate is None:
            return state

        incoming_attrs = self._copy_public_attrs(candidate)
        current_attrs = self._copy_public_attrs(state)

        for key, value in incoming_attrs.items():
            if key in {"pair_name"}:
                continue

            if key == "trades":
                merged_trades = self._merge_trade_history(current_attrs.get("trades", []), value or [])
                self._safe_set_attr(state, "trades", merged_trades)
                continue

            if key in {"metadata", "extra"}:
                merged_dict = self._merge_dict(current_attrs.get(key, {}) or {}, value or {})
                self._safe_set_attr(state, key, merged_dict)
                continue

            if key == "last_trade":
                if value is not None:
                    self._safe_set_attr(state, key, value)
                continue

            self._safe_set_attr(state, key, deepcopy(value))

        if self._get_attr(state, "last_trade", None) is None:
            trades = list(self._get_attr(state, "trades", []) or [])
            if trades:
                self._safe_set_attr(state, "last_trade", trades[-1])

        self._set_pair_runtime_metadata(pair, state, self._services.get(pair))
        return state

    def _assert_service_pair_integrity(self, pair: str, service: RobotService) -> None:
        pair = _normalize_pair(pair)

        if service is None:
            raise ValueError(f"Cannot use empty service for pair {pair}")

        service_pair = _normalize_pair(getattr(service, "pair", "") or "")
        if service_pair and service_pair != pair:
            raise ValueError(f"Service pair mismatch: attach {pair}, service bound to {service_pair}")

        existing_pair = self._service_to_pair.get(id(service))
        if existing_pair and existing_pair != pair:
            raise ValueError(
                f"Same RobotService instance already attached to {existing_pair}; "
                f"service reuse across pairs is not allowed"
            )

        router = getattr(service, "router", None)
        router_pair = _normalize_pair(getattr(router, "pair", "") or "") if router is not None else ""
        if router_pair and router_pair != pair:
            mismatch_key = f"{pair}:{router_pair}"
            if mismatch_key not in self._warned_router_pair_mismatch:
                self._warned_router_pair_mismatch.add(mismatch_key)
            raise ValueError(f"Router pair mismatch: attach {pair}, router bound to {router_pair}")

    def _component_requirements(self, service: RobotService) -> Dict[str, bool]:
        mode = str(
            getattr(service, "trading_mode", None)
            or getattr(service, "mode", None)
            or self._runtime_mode
            or "paper"
        ).lower().strip()

        return {
            "coinmate_client": mode == "live",
            "router": mode == "live",
            "risk_manager": False,
        }

    def _validate_service_attachment(self, pair: str, service: RobotService) -> None:
        pair = _normalize_pair(pair)

        self._assert_service_pair_integrity(pair, service)

        requirements = self._component_requirements(service)
        warned = self._warned_missing_components.setdefault(pair, set())

        for attr_name, required in requirements.items():
            component = getattr(service, attr_name, None)
            if component is not None:
                continue

            if required:
                raise ValueError(
                    f"Cannot attach service for {pair} without required component {attr_name}"
                )

            if attr_name not in warned:
                mode = str(
                    getattr(service, "trading_mode", None)
                    or getattr(service, "mode", None)
                    or self._runtime_mode
                    or "paper"
                ).lower().strip()
                logger.info(
                    "Service %s attached without optional %s in %s mode",
                    pair,
                    attr_name,
                    mode,
                )
                warned.add(attr_name)

    def _extract_execution_backend_snapshot(self, service: Optional[RobotService]) -> Dict[str, Any]:
        if service is None:
            return {
                "available": False,
                "reason": "service_missing",
            }

        snapshot = None
        accessor = getattr(service, "get_runtime_snapshot", None)
        if accessor and callable(accessor):
            try:
                snapshot = accessor()
            except Exception:
                snapshot = None

        if not isinstance(snapshot, dict):
            return {
                "available": False,
                "reason": "runtime_snapshot_missing",
            }

        backend = snapshot.get("execution_backend")
        if isinstance(backend, dict):
            return deepcopy(backend)

        return {
            "available": False,
            "reason": "execution_backend_missing",
        }

    def _derive_backend_status(self, backend: Dict[str, Any], runtime_mode: Optional[str] = None) -> Dict[str, Any]:
        mode = str(runtime_mode or self._runtime_mode or "paper").lower().strip()

        if not isinstance(backend, dict):
            return {
                "health_status": "unknown",
                "degraded": False,
                "reason": "backend_not_dict",
            }

        client = backend.get("client") if isinstance(backend.get("client"), dict) else {}
        router = backend.get("router") if isinstance(backend.get("router"), dict) else {}
        identity = backend.get("account_identity") if isinstance(backend.get("account_identity"), dict) else {}

        client_available = bool(client.get("available", False))
        router_available = bool(router.get("available", False))
        identity_available = bool(identity.get("available", False))

        client_last_error = client.get("last_error")
        client_failure_count = 0
        try:
            client_failure_count = int(client.get("failure_count") or 0)
        except Exception:
            client_failure_count = 0

        degraded = False
        reasons: List[str] = []

        if mode == "live":
            if not client_available:
                degraded = True
                reasons.append("client_unavailable")
            if not router_available:
                degraded = True
                reasons.append("router_unavailable")
            if not identity_available:
                degraded = True
                reasons.append("identity_unavailable")
            if client_last_error not in (None, "", False, 0):
                degraded = True
                reasons.append("client_last_error")
            if client_failure_count > 0 and not client.get("last_success_ts"):
                degraded = True
                reasons.append("client_no_success_yet")
        else:
            if client_available and client_last_error not in (None, "", False, 0):
                degraded = True
                reasons.append("client_last_error")
            if client_available and client_failure_count > 0 and not client.get("last_success_ts"):
                degraded = True
                reasons.append("client_no_success_yet")

        return {
            "health_status": "degraded" if degraded else "healthy",
            "degraded": degraded,
            "reason": ",".join(reasons) if reasons else ("paper_mode" if mode != "live" else None),
        }

    def _set_pair_runtime_metadata(self, pair: str, state: RobotState, service: Optional[RobotService]) -> None:
        metadata = dict(self._get_attr(state, "metadata", {}) or {})
        control = self._control_plane.as_dict(pair=pair)
        raw_status = self._normalize_status_name(self._get_attr(state, "status", None))
        effective_status = self._effective_pair_status(pair, state)
        task_active = self._is_pair_task_active(pair)

        metadata["pair"] = pair
        metadata["account_scope"] = "isolated"
        metadata["orchestrator_started_at"] = metadata.get("orchestrator_started_at") or _utc_now_iso()
        metadata["runtime_mode"] = control.get("mode", self._runtime_mode)
        metadata["service_attached"] = service is not None
        metadata["service_id"] = id(service) if service is not None else None
        metadata["has_router"] = bool(getattr(service, "router", None)) if service is not None else False
        metadata["has_client"] = bool(getattr(service, "coinmate_client", None)) if service is not None else False
        metadata["has_risk_manager"] = bool(getattr(service, "risk_manager", None)) if service is not None else False
        metadata["control"] = control
        metadata["last_ok_at"] = (
            datetime.fromtimestamp(self._pair_last_ok_ts[pair], tz=timezone.utc).isoformat()
            if pair in self._pair_last_ok_ts
            else metadata.get("last_ok_at")
        )
        metadata["last_cycle_started_at"] = (
            datetime.fromtimestamp(self._pair_last_cycle_started_ts[pair], tz=timezone.utc).isoformat()
            if pair in self._pair_last_cycle_started_ts
            else metadata.get("last_cycle_started_at")
        )
        metadata["last_cycle_finished_at"] = (
            datetime.fromtimestamp(self._pair_last_cycle_finished_ts[pair], tz=timezone.utc).isoformat()
            if pair in self._pair_last_cycle_finished_ts
            else metadata.get("last_cycle_finished_at")
        )
        metadata["consecutive_cycle_errors"] = self._pair_error_count.get(pair, 0)
        metadata["restart_backoff_sec"] = self._pair_restart_backoff.get(pair, 0.0)

        execution_backend = self._extract_execution_backend_snapshot(service)
        metadata["execution_backend"] = execution_backend

        backend_status = self._derive_backend_status(execution_backend, runtime_mode=control.get("mode", self._runtime_mode))
        metadata["execution_backend_status"] = backend_status["health_status"]
        metadata["execution_backend_degraded"] = backend_status["degraded"]
        metadata["execution_backend_reason"] = backend_status["reason"]
        metadata["orchestrator_running"] = bool(self._running)
        metadata["pair_task_active"] = task_active
        metadata["state_status_raw"] = raw_status or None
        metadata["effective_status"] = effective_status

        self._safe_set_attr(state, "metadata", metadata)

        extra = dict(self._get_attr(state, "extra", {}) or {})
        extra["isolated_runtime"] = True
        extra["stale_after_sec"] = settings.robot_stale_after_sec
        extra["cycle_timeout_sec"] = settings.robot_cycle_timeout_sec
        extra["max_trades_in_memory"] = settings.robot_max_trades_in_memory
        extra["execution_backend_status"] = backend_status["health_status"]
        extra["execution_backend_reason"] = backend_status["reason"]
        extra["control_mode"] = control.get("mode", self._runtime_mode)
        extra["control_armed"] = bool(control.get("armed", False))
        extra["control_reduce_only"] = bool(control.get("reduce_only", False))
        extra["control_pause_new_trades"] = bool(control.get("pause_new_trades", False))
        extra["control_emergency_stop"] = bool(control.get("emergency_stop", False))
        extra["orchestrator_running"] = bool(self._running)
        extra["pair_task_active"] = task_active
        extra["effective_status"] = effective_status
        self._safe_set_attr(state, "extra", extra)
    def _record_pair_watchdog(
        self,
        pair: str,
        *,
        status: str,
        backend_status: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        stale: bool = False,
    ) -> None:
        payload: Dict[str, Any] = {
            "status": str(status or "").upper(),
            "reason": reason,
            "stale": bool(stale),
            "backend": None,
            "restart_backoff_sec": self._pair_restart_backoff.get(pair),
        }
        last_ok = self._pair_last_ok_ts.get(pair)
        if last_ok:
            try:
                payload["last_ok"] = datetime.fromtimestamp(last_ok, tz=timezone.utc).isoformat()
            except Exception:
                payload["last_ok"] = last_ok
        if backend_status:
            payload["backend"] = {
                "health_status": backend_status.get("health_status"),
                "degraded": backend_status.get("degraded"),
                "reason": backend_status.get("reason"),
            }
        try:
            record_pair_health(pair, payload)
        except Exception:
            pass


    async def _run_pair_cycle(self, pair: str) -> None:
        service = self._services[pair]
        interval_sec = max(0.2, float(getattr(settings, "robot_cycle_interval_sec", 2.0) or 2.0))
        timeout_sec = max(1.0, float(getattr(settings, "robot_cycle_timeout_sec", 10.0) or 10.0))
        stale_after_sec = max(interval_sec * 3.0, float(getattr(settings, "robot_stale_after_sec", 60.0) or 60.0))
        lock = self._locks.setdefault(pair, asyncio.Lock())

        state = self._ensure_state(pair)
        self._safe_set_attr(state, "status", "running")
        self._set_pair_runtime_metadata(pair, state, service)
        self._pair_last_status[pair] = "running"

        while self._running and pair in self._services and self._tasks.get(pair) is asyncio.current_task():
            try:
                async with lock:
                    now_ts = time.time()
                    self._pair_last_cycle_started_ts[pair] = now_ts
                    self._safe_set_attr(state, "last_cycle_started_at", _utc_now_iso())
                    self._set_pair_runtime_metadata(pair, state, service)

                    last_ok = self._pair_last_ok_ts.get(pair)
                    if last_ok is not None and (now_ts - last_ok) > stale_after_sec:
                        if self._pair_last_status.get(pair) != "stale":
                            self._append_event(
                                pair,
                                "pair_stale_detected",
                                stale_for_sec=round(now_ts - last_ok, 3),
                                stale_after_sec=stale_after_sec,
                            )
                        self._safe_set_attr(state, "status", "stale")
                        self._pair_last_status[pair] = "stale"
                        logger.warning(
                            "Pair %s stale detected: %.3fs since last successful cycle",
                            pair,
                            now_ts - last_ok,
                        )

                    control = self._control_state(pair)
                    runtime_mode = str(control.mode or self._runtime_mode).lower()
                    self._runtime_mode = str(self._control_plane.get().mode or runtime_mode).lower()

                    if control.kill_switch:
                        self._safe_set_attr(state, "status", "emergency_stop")
                        if self._pair_last_status.get(pair) != "emergency_stop":
                            self._append_event(pair, "emergency_stop_active", reason=control.reason)
                        self._pair_last_status[pair] = "emergency_stop"
                        self._set_pair_runtime_metadata(pair, state, service)
                        self._persist_pair_runtime_state(pair)
                        await asyncio.sleep(interval_sec)
                        continue

                    if control.pause_new_trades:
                        self._safe_set_attr(state, "status", "paused")
                        if self._pair_last_status.get(pair) != "paused":
                            self._append_event(pair, "pair_paused", reason=control.reason)
                        self._pair_last_status[pair] = "paused"
                        self._set_pair_runtime_metadata(pair, state, service)
                        self._persist_pair_runtime_state(pair)
                        await asyncio.sleep(interval_sec)
                        continue

                    try:
                        if hasattr(service, "set_trading_mode"):
                            service.set_trading_mode(runtime_mode)
                        elif hasattr(service, "trading_mode"):
                            setattr(service, "trading_mode", runtime_mode)
                    except Exception as e:
                        logger.warning("Failed to synchronize trading mode for %s: %s", pair, e)

                    try:
                        result = await asyncio.wait_for(
                            self._run_service_step(service),
                            timeout=timeout_sec,
                        )
                    except asyncio.TimeoutError:
                        raise RuntimeError(f"service_step_timeout>{timeout_sec:.1f}s")

                    candidate_state = self._extract_state_candidate(pair, result, service)
                    state = self._update_state_incremental(pair, candidate_state)

                    backend = self._extract_execution_backend_snapshot(service)
                    backend_status = self._derive_backend_status(backend, runtime_mode=runtime_mode)

                    if backend_status["degraded"]:
                        self._safe_set_attr(state, "status", "degraded")
                        self._safe_set_attr(state, "last_error", backend_status["reason"])
                        if self._pair_last_status.get(pair) != "degraded":
                            self._append_event(
                                pair,
                                "execution_backend_degraded",
                                reason=backend_status["reason"],
                            )
                        self._pair_last_status[pair] = "degraded"
                    else:
                        self._safe_set_attr(state, "status", "running")
                        self._safe_set_attr(state, "last_error", None)
                        self._pair_last_status[pair] = "running"

                    ok_ts = time.time()
                    self._pair_last_ok_ts[pair] = ok_ts
                    self._pair_last_cycle_finished_ts[pair] = ok_ts
                    self._pair_error_count[pair] = 0
                    self._pair_restart_backoff[pair] = 0.0

                    self._safe_set_attr(state, "last_cycle_finished_at", _utc_now_iso())
                    self._safe_set_attr(state, "last_cycle_ok", ok_ts)
                    self._set_pair_runtime_metadata(pair, state, service)
                    self._persist_pair_runtime_state(pair)

                    set_gauge(f"robot.{pair}.status", 1.0 if not backend_status["degraded"] else 0.5)
                    self._last_errors.pop(pair, None)

                    try:
                        telemetry_hub.emit(
                            "orchestrator_cycle_ok",
                            {
                                "pair": pair,
                                "ts": _utc_now_iso(),
                                "backend_status": backend_status["health_status"],
                                "backend_reason": backend_status["reason"],
                            },
                        )
                    except Exception:
                        pass

            except asyncio.CancelledError:
                self._safe_set_attr(state, "status", "stopped")
                self._safe_set_attr(state, "last_cycle_finished_at", _utc_now_iso())
                self._set_pair_runtime_metadata(pair, state, service)
                self._persist_pair_runtime_state(pair)
                self._pair_last_status[pair] = "stopped"
                raise

            except Exception as e:
                self._pair_error_count[pair] = self._pair_error_count.get(pair, 0) + 1
                restart_delay = min(
                    float(getattr(settings, "robot_max_restart_backoff_sec", 60.0) or 60.0),
                    float(2 ** min(self._pair_error_count[pair], 6)),
                )
                self._pair_restart_backoff[pair] = restart_delay
                self._pair_last_cycle_finished_ts[pair] = time.time()

                logger.exception("Pair cycle error for %s: %s", pair, e)
                self._safe_set_attr(state, "status", "error")
                self._safe_set_attr(state, "last_error", str(e))
                self._safe_set_attr(state, "last_cycle_finished_at", _utc_now_iso())
                self._last_errors[pair] = str(e)
                self._pair_last_status[pair] = "error"
                self._set_pair_runtime_metadata(pair, state, service)
                self._persist_pair_runtime_state(pair)
                self._append_event(
                    pair,
                    "cycle_error",
                    error=str(e),
                    error_count=self._pair_error_count[pair],
                    restart_backoff_sec=restart_delay,
                )
                inc_error(f"robot.{pair}.cycle_error")

                try:
                    telemetry_hub.emit(
                        "orchestrator_cycle_error",
                        {
                            "pair": pair,
                            "error": str(e),
                            "ts": _utc_now_iso(),
                            "error_count": self._pair_error_count[pair],
                            "restart_backoff_sec": restart_delay,
                        },
                    )
                except Exception:
                    pass

                await asyncio.sleep(restart_delay)

            await asyncio.sleep(interval_sec)

        self._safe_set_attr(state, "status", "stopped")
        self._safe_set_attr(state, "last_cycle_finished_at", _utc_now_iso())
        self._set_pair_runtime_metadata(pair, state, service)
        self._persist_pair_runtime_state(pair)
        self._pair_last_status[pair] = "stopped"

    async def _run_service_step(self, service: RobotService) -> Any:
        for method_name in ("step", "run_cycle", "tick", "cycle_once", "process_once"):
            method = getattr(service, method_name, None)
            if method and callable(method):
                result = method()
                if inspect.isawaitable(result):
                    result = await result
                return result

        if callable(service):
            result = service()
            if inspect.isawaitable(result):
                result = await result
            return result

        return None

    def _extract_state_candidate(self, pair: str, result: Any, service: RobotService) -> Optional[RobotState]:
        candidate = self._coerce_state_candidate(result)
        if candidate is not None:
            return candidate

        for accessor_name in ("get_state", "snapshot_state"):
            accessor = getattr(service, accessor_name, None)
            if accessor and callable(accessor):
                try:
                    value = accessor()
                    if inspect.isawaitable(value):
                        continue
                    candidate = self._coerce_state_candidate(value)
                    if candidate is not None:
                        return candidate
                except Exception:
                    continue

        for attr_name in ("state", "last_state", "_state"):
            value = getattr(service, attr_name, None)
            candidate = self._coerce_state_candidate(value)
            if candidate is not None:
                return candidate

        snapshot = None
        for accessor_name in ("get_runtime_snapshot", "runtime_snapshot", "snapshot"):
            accessor = getattr(service, accessor_name, None)
            if accessor and callable(accessor):
                try:
                    snapshot = accessor()
                    if inspect.isawaitable(snapshot):
                        snapshot = None
                    if snapshot is not None:
                        break
                except Exception:
                    continue

        if snapshot is None and hasattr(service, "__dict__"):
            snapshot = self._copy_public_attrs(service)

        if isinstance(snapshot, dict):
            try:
                synthetic = self._new_robot_state(pair)
                self._safe_set_attr(synthetic, "status", "running")
                for key, value in snapshot.items():
                    if str(key).startswith("_"):
                        continue
                    self._safe_set_attr(synthetic, key, deepcopy(value))
                return synthetic
            except Exception:
                return None

        return None

    def get_dashboard_snapshot(self, market_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._runtime_context is None:
            return {
                "timestamp": _utc_now_iso(),
                "global": {"mode": self._runtime_mode, "armed": False, "robot_status": "STOPPED", "emergency_stop": False, "readiness": {}, "live_readiness": False},
                "summary": {"portfolio_value": None, "pnl_today": None, "exposure": None, "open_trades": 0, "holdings": [], "crypto_pct": None, "fiat_pct": None},
                "market": market_summary or {},
                "pairs": [],
            }
        builder = GlobalDashboardSnapshotBuilder(self._runtime_context, self, control_plane=self._control_plane, market_summary=market_summary)
        return builder.build()


_global_orchestrator: Optional[GlobalOrchestrator] = None


def get_global_orchestrator() -> GlobalOrchestrator:
    try:
        from app.runtime.runtime_context import get_runtime_context
        return get_runtime_context().get_global_orchestrator()
    except Exception:
        global _global_orchestrator
        if _global_orchestrator is None:
            _global_orchestrator = GlobalOrchestrator()
        return _global_orchestrator