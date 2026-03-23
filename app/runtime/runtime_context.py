from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from app.orchestrator.global_orchestrator import GlobalOrchestrator
from app.services.robot_service import RobotService, load_coinmate_creds_from_env
from app.storage import db as storage_db

from app.core.execution.coinmate_client import CoinmateClient
from app.core.execution.coinmate_router import CoinmateRouter
from app.core.risk.risk import RiskManager
from app.core.engine.runtime_pair_engine import RuntimePairEngine
from app.core.control_plane import ControlPlane

logger = logging.getLogger("robot.runtime")


DEFAULT_PAIRS: List[str] = [
    "BTC_EUR",
    "BTC_CZK",
    "ETH_EUR",
    "ETH_CZK",
    "ADA_CZK",
]


def _normalize_pair(value: str) -> str:
    return str(value or "").strip().upper()


def _parse_pairs_from_env() -> List[str]:
    raw = (os.getenv("ROBOT_PAIRS", "") or "").strip()
    if not raw:
        return list(DEFAULT_PAIRS)

    pairs: List[str] = []
    for item in raw.split(","):
        pair = _normalize_pair(item)
        if pair and pair not in pairs:
            pairs.append(pair)
    return pairs or list(DEFAULT_PAIRS)


def _resolve_primary_pair(pairs: List[str]) -> str:
    requested = _normalize_pair(os.getenv("PRIMARY_PAIR", ""))
    if requested and requested in pairs:
        return requested

    if requested and requested not in pairs:
        logger.warning(
            "PRIMARY_PAIR=%s is not in ROBOT_PAIRS; falling back to first configured pair",
            requested,
        )

    return pairs[0]


def _build_execution_stack(pair: str, creds):
    client = None
    router = None
    risk = None

    try:
        if isinstance(creds, dict):
            api_key = str(creds.get("api_key") or "").strip()
            api_secret = str(creds.get("api_secret") or "").strip()
            client_id = str(creds.get("client_id") or "").strip()

            if api_key and api_secret and client_id:
                client = CoinmateClient(
                    api_key=api_key,
                    api_secret=api_secret,
                    client_id=client_id,
                )
                router = CoinmateRouter(client, pair=pair)
            else:
                logger.warning("Incomplete Coinmate credentials for %s", pair)
    except Exception:
        logger.exception("Failed to build Coinmate execution stack for %s", pair)

    try:
        risk = RiskManager(pair=pair)
    except Exception:
        logger.exception("Failed to initialize RiskManager for %s", pair)

    return client, router, risk


def _build_robot_service_for_pair(pair: str) -> RobotService:
    pair = _normalize_pair(pair)
    creds = None

    try:
        creds = load_coinmate_creds_from_env(pair)
        if creds:
            logger.info("Loaded Coinmate credentials for pair %s", pair)
    except Exception:
        logger.warning("No private API credentials for %s", pair)

    client, router, risk = _build_execution_stack(pair, creds)

    service = RobotService(
        pair=pair,
        coinmate_client=client,
        router=router,
        risk_manager=risk,
        creds=creds,
        trading_mode=os.getenv("TRADING_MODE", "paper"),
        market_data_source=os.getenv("MARKET_DATA_SOURCE", "coinmate_ticker"),
        quote_ccy=pair.split("_", 1)[1] if "_" in pair else None,
    )

    bind_method = getattr(service, "bind_runtime_dependencies", None)
    if callable(bind_method):
        try:
            bind_method(
                coinmate_client=client,
                router=router,
                risk_manager=risk,
                market_data_source=os.getenv("MARKET_DATA_SOURCE", "coinmate_ticker"),
                creds=creds,
            )
        except Exception:
            logger.exception("bind_runtime_dependencies failed for pair %s", pair)
    else:
        service.coinmate_client = client
        service.router = router
        service.risk_manager = risk
    return service


def _validate_service(service: RobotService, pair: str, *, log_warnings: bool = True):
    if service is None:
        raise RuntimeError(f"RobotService missing for pair {pair}")

    if not hasattr(service, "pair"):
        raise RuntimeError(f"RobotService for {pair} missing pair attribute")

    if service.pair != pair:
        raise RuntimeError(
            f"RobotService pair mismatch: expected {pair} got {service.pair}"
        )

    if log_warnings and getattr(service, "router", None) is None:
        logger.warning("Router missing for pair %s (paper trading only)", pair)

    if log_warnings and getattr(service, "risk_manager", None) is None:
        logger.warning("Risk manager missing for pair %s", pair)


def _build_robot_service_map(pairs: List[str]) -> Dict[str, RobotService]:
    services: Dict[str, RobotService] = {}
    for pair in pairs:
        try:
            service = _build_robot_service_for_pair(pair)
            _validate_service(service, pair, log_warnings=False)
            services[pair] = service
        except Exception:
            logger.exception("Failed to build RobotService for pair %s", pair)
    return services


def _attach_services_to_orchestrator(
    orchestrator: GlobalOrchestrator,
    service_map: Dict[str, RobotService],
    primary_pair: str,
):
    primary_service = service_map.get(primary_pair)
    if primary_service is None:
        raise RuntimeError("Primary pair service missing")

    orchestrator.robot_service = primary_service

    for pair, service in service_map.items():
        try:
            if hasattr(orchestrator, "attach_service"):
                orchestrator.attach_service(pair, service)
                continue
        except Exception:
            logger.exception("attach_service failed for %s", pair)

        try:
            if hasattr(orchestrator, "_services"):
                existing = getattr(orchestrator, "_services", {}).get(pair)
                if existing is None:
                    orchestrator._services[pair] = service
        except Exception:
            logger.exception("Failed fallback attach for %s", pair)

    orchestrator.primary_pair = primary_pair


@dataclass
class PairRuntimeBinding:
    pair: str
    service: RobotService
    runtime_data: Dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "pair": self.pair,
            "runtime_data": json.loads(json.dumps(self.runtime_data, ensure_ascii=False, default=str)),
        }


class RuntimeContext:
    KV_PREFIX = "runtime_pair_state"

    def __init__(self):
        self.global_orchestrator: Optional[GlobalOrchestrator] = None
        self.primary_pair: Optional[str] = None
        self.pairs: List[str] = []
        self.robot_services: Dict[str, RobotService] = {}
        self.pair_bindings: Dict[str, PairRuntimeBinding] = {}
        self.pair_engines: Dict[str, RuntimePairEngine] = {}
        self._initialized = False
        self._initializing = False
        self._lock = threading.RLock()
        self._warned_missing_router: Set[str] = set()
        self._warned_missing_risk_manager: Set[str] = set()
        self._control_plane: Optional[ControlPlane] = None


    def _resolve_control_plane(self) -> ControlPlane:
        control_plane = self._control_plane
        if control_plane is not None:
            return control_plane

        orchestrator = self.global_orchestrator
        if orchestrator is not None:
            orchestrator_control_plane = getattr(orchestrator, "_control_plane", None)
            if orchestrator_control_plane is not None:
                self._control_plane = orchestrator_control_plane
                return orchestrator_control_plane

        control_plane = ControlPlane()
        self._control_plane = control_plane

        if orchestrator is not None:
            try:
                orchestrator._control_plane = control_plane
            except Exception:
                logger.exception("Failed to share ControlPlane with GlobalOrchestrator")

        return control_plane

    def _build_pair_engine(
        self,
        pair: str,
        service: RobotService,
        orchestrator: GlobalOrchestrator,
    ) -> RuntimePairEngine:
        return RuntimePairEngine(
            pair=pair,
            service=service,
            runtime_context=self,
            orchestrator=orchestrator,
            control_plane=self._resolve_control_plane(),
        )

    def _attach_service_to_global_orchestrator(self, pair: str, service: RobotService) -> None:
        pair = _normalize_pair(pair)
        orchestrator = self.global_orchestrator
        if orchestrator is None:
            return

        try:
            _validate_service(service, pair, log_warnings=False)
        except Exception:
            logger.exception("Cannot attach invalid RobotService for pair %s", pair)
            raise

        attached = False
        attach_method = getattr(orchestrator, "attach_service", None)
        if callable(attach_method):
            try:
                attach_method(pair, service)
                attached = True
            except Exception:
                logger.exception("attach_service failed for pair %s", pair)

        if not attached:
            try:
                services = getattr(orchestrator, "_services", None)
                if isinstance(services, dict):
                    services[pair] = service
                    attached = True
            except Exception:
                logger.exception("Failed fallback attach for pair %s", pair)

        if not attached:
            raise RuntimeError(f"Unable to attach RobotService to orchestrator for pair {pair}")

        try:
            if getattr(orchestrator, "robot_service", None) is None:
                orchestrator.robot_service = service
        except Exception:
            logger.exception("Failed to set primary robot_service on orchestrator for %s", pair)

    def initialize(self):
        with self._lock:
            if self._initialized:
                return
            if self._initializing:
                return

            self._initializing = True
            try:
                logger.info("Initializing RuntimeContext")

                pairs = _parse_pairs_from_env()
                if not pairs:
                    raise RuntimeError("No trading pairs configured")

                primary_pair = _resolve_primary_pair(pairs)
                service_map = _build_robot_service_map(pairs)
                if primary_pair not in service_map:
                    raise RuntimeError("Primary pair has no RobotService")

                self.pairs = list(pairs)
                self.primary_pair = primary_pair
                self.robot_services = dict(service_map)
                self.pair_bindings = {}
                self.pair_engines = {}

                for pair in pairs:
                    service = service_map[pair]
                    binding = PairRuntimeBinding(
                        pair=pair,
                        service=service,
                        runtime_data=self._load_pair_runtime_data(pair),
                    )
                    self.pair_bindings[pair] = binding

                self._initialized = True

                orchestrator = GlobalOrchestrator(runtime_context=self)
                self.global_orchestrator = orchestrator
                self._control_plane = getattr(orchestrator, "_control_plane", None) or ControlPlane()
                try:
                    orchestrator._control_plane = self._control_plane
                except Exception:
                    logger.exception("Failed to bind shared ControlPlane to GlobalOrchestrator")

                _attach_services_to_orchestrator(orchestrator, service_map, primary_pair)
                self.pair_engines = {
                    pair: self._build_pair_engine(pair, service_map[pair], orchestrator)
                    for pair in pairs
                }

                logger.info(
                    "RuntimeContext initialized primary=%s pairs=%s",
                    primary_pair,
                    pairs,
                )
            except Exception:
                self._initialized = False
                raise
            finally:
                self._initializing = False

    def _kv_key(self, pair: str) -> str:
        return f"{self.KV_PREFIX}:{_normalize_pair(pair)}"

    def _load_pair_runtime_data(self, pair: str) -> Dict[str, Any]:
        raw = storage_db.kv_get(self._kv_key(pair), "")
        if raw in (None, ""):
            return {"pair": _normalize_pair(pair), "config": {}, "state": {}, "service": {}}

        try:
            payload = json.loads(str(raw))
            if isinstance(payload, dict):
                payload.setdefault("pair", _normalize_pair(pair))
                payload.setdefault("config", {})
                payload.setdefault("state", {})
                payload.setdefault("service", {})
                return payload
        except Exception:
            logger.exception("Failed to decode persisted runtime data for %s", pair)

        return {"pair": _normalize_pair(pair), "config": {}, "state": {}, "service": {}}

    def _warn_missing_components_once(self, pair: str, service: RobotService) -> None:
        pair = _normalize_pair(pair)

        if getattr(service, "router", None) is None and pair not in self._warned_missing_router:
            logger.warning("Router missing for pair %s (paper trading only)", pair)
            self._warned_missing_router.add(pair)

        if getattr(service, "risk_manager", None) is None and pair not in self._warned_missing_risk_manager:
            logger.warning("Risk manager missing for pair %s", pair)
            self._warned_missing_risk_manager.add(pair)

    def persist_pair_runtime_data(self, pair: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        pair = _normalize_pair(pair)
        with self._lock:
            binding = self.pair_bindings.get(pair)
            if binding is None:
                service = self.robot_services.get(pair)
                if service is None:
                    raise KeyError(f"Runtime binding missing for pair {pair}")
                binding = PairRuntimeBinding(
                    pair=pair,
                    service=service,
                    runtime_data=self._load_pair_runtime_data(pair),
                )
                self.pair_bindings[pair] = binding

            merged = dict(binding.runtime_data or {})
            for key, value in (payload or {}).items():
                merged[key] = value
            merged["pair"] = pair

            serialized = json.dumps(merged, ensure_ascii=False, default=str)
            storage_db.kv_set(self._kv_key(pair), serialized)
            binding.runtime_data = merged
            return dict(merged)

    def update_pair_config(self, pair: str, **fields: Any) -> Dict[str, Any]:
        pair = _normalize_pair(pair)
        current = self.get_pair_runtime_data(pair)
        config = dict(current.get("config", {}) or {})
        config.update(fields)
        return self.persist_pair_runtime_data(pair, {**current, "config": config})

    def get_pair_runtime_data(self, pair: str) -> Dict[str, Any]:
        if not self._initialized:
            self.initialize()

        pair = _normalize_pair(pair)
        with self._lock:
            binding = self.pair_bindings.get(pair)
            if binding is None:
                raise KeyError(f"Runtime binding missing for pair {pair}")
            return dict(binding.runtime_data or {})

    def get_runtime_configs(self) -> Dict[str, Dict[str, Any]]:
        if not self._initialized:
            self.initialize()

        with self._lock:
            return {
                pair: dict(binding.runtime_data.get("config", {}) or {})
                for pair, binding in self.pair_bindings.items()
            }

    def get_global_orchestrator(self) -> GlobalOrchestrator:
        if not self._initialized:
            self.initialize()
        return self.global_orchestrator

    def get_robot_service(self, pair: Optional[str] = None) -> RobotService:
        if not self._initialized:
            self.initialize()

        target = _normalize_pair(pair or self.primary_pair or "")
        if not target:
            raise KeyError("RobotService target pair is empty")

        with self._lock:
            service = self.robot_services.get(target)
            if service is None:
                raise KeyError(f"RobotService not found for pair {target}")
            return service

    def get_pairs(self) -> List[str]:
        if not self._initialized:
            self.initialize()
        return list(self.pairs)

    def get_or_create_pair_binding(self, pair: str) -> PairRuntimeBinding:
        if not self._initialized:
            self.initialize()

        pair = _normalize_pair(pair)
        if not pair:
            raise KeyError("Runtime binding missing for empty pair")

        with self._lock:
            binding = self.pair_bindings.get(pair)
            if binding is not None:
                return binding

            service = self.robot_services.get(pair)
            if service is None:
                service = _build_robot_service_for_pair(pair)
                _validate_service(service, pair, log_warnings=False)
                self.robot_services[pair] = service
                self._attach_service_to_global_orchestrator(pair, service)

            self._warn_missing_components_once(pair, service)

            binding = PairRuntimeBinding(
                pair=pair,
                service=service,
                runtime_data=self._load_pair_runtime_data(pair),
            )
            self.pair_bindings[pair] = binding

            if pair not in self.pairs:
                self.pairs.append(pair)

            return binding

    def get_pair_binding(self, pair: str) -> PairRuntimeBinding:
        if not self._initialized:
            self.initialize()

        pair = _normalize_pair(pair)
        with self._lock:
            binding = self.pair_bindings.get(pair)
            if binding is None:
                raise KeyError(f"Runtime binding missing for pair {pair}")
            return binding

    def get_pair_engine(self, pair: str) -> RuntimePairEngine:
        if not self._initialized:
            self.initialize()
        pair = _normalize_pair(pair)
        with self._lock:
            engine = self.pair_engines.get(pair)
            if engine is None:
                binding = self.get_or_create_pair_binding(pair)
                service = binding.service
                orchestrator = self.get_global_orchestrator()
                self._attach_service_to_global_orchestrator(pair, service)
                engine = self._build_pair_engine(pair, service, orchestrator)
                self.pair_engines[pair] = engine
            return engine

    def get_pair_engines(self) -> Dict[str, RuntimePairEngine]:
        if not self._initialized:
            self.initialize()
        return {pair: self.get_pair_engine(pair) for pair in self.get_pairs()}


_runtime: Optional[RuntimeContext] = None
_runtime_lock = threading.Lock()


def get_runtime_context() -> RuntimeContext:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = RuntimeContext()
            _runtime.initialize()
        return _runtime


def get_global_orchestrator() -> GlobalOrchestrator:
    ctx = get_runtime_context()
    return ctx.get_global_orchestrator()


def get_orchestrator():
    ctx = get_runtime_context()
    return ctx.get_global_orchestrator()