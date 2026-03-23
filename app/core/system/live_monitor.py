from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class LiveSnapshot:
    pair: str
    mode: str
    armed: bool
    market_data_ok: bool
    execution_ok: bool
    positions_synced: bool
    balances_synced: bool
    risk_ok: bool
    ready: bool
    reason: Optional[str]
    updated_ts: float


class LiveMonitor:
    """
    Deterministic live-readiness monitor.

    Purpose:
    - aggregate live readiness into one truthful payload
    - never infer readiness from a single flag
    - keep pair isolation
    """

    def __init__(self, pair: str) -> None:
        self.pair = str(pair).upper().strip()
        self._snapshot = LiveSnapshot(
            pair=self.pair,
            mode="paper",
            armed=False,
            market_data_ok=False,
            execution_ok=False,
            positions_synced=False,
            balances_synced=False,
            risk_ok=False,
            ready=False,
            reason="init",
            updated_ts=time.time(),
        )

    def update(
        self,
        *,
        mode: str,
        armed: bool,
        market_data_ok: bool,
        execution_ok: bool,
        positions_synced: bool,
        balances_synced: bool,
        risk_ok: bool,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_mode = self._normalize_mode(mode)

        ready = (
            normalized_mode == "live"
            and bool(armed)
            and bool(market_data_ok)
            and bool(execution_ok)
            and bool(positions_synced)
            and bool(balances_synced)
            and bool(risk_ok)
        )

        if reason in (None, ""):
            reason = self._derive_reason(
                mode=normalized_mode,
                armed=bool(armed),
                market_data_ok=bool(market_data_ok),
                execution_ok=bool(execution_ok),
                positions_synced=bool(positions_synced),
                balances_synced=bool(balances_synced),
                risk_ok=bool(risk_ok),
                ready=bool(ready),
            )

        self._snapshot = LiveSnapshot(
            pair=self.pair,
            mode=normalized_mode,
            armed=bool(armed),
            market_data_ok=bool(market_data_ok),
            execution_ok=bool(execution_ok),
            positions_synced=bool(positions_synced),
            balances_synced=bool(balances_synced),
            risk_ok=bool(risk_ok),
            ready=bool(ready),
            reason=(str(reason) if reason not in (None, "") else None),
            updated_ts=time.time(),
        )
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self._snapshot)

    def _derive_reason(
        self,
        *,
        mode: str,
        armed: bool,
        market_data_ok: bool,
        execution_ok: bool,
        positions_synced: bool,
        balances_synced: bool,
        risk_ok: bool,
        ready: bool,
    ) -> str:
        if mode != "live":
            return "not_live_mode"
        if not armed:
            return "not_armed"
        if not market_data_ok:
            return "market_data_not_ready"
        if not execution_ok:
            return "execution_not_ready"
        if not balances_synced:
            return "balances_not_synced"
        if not positions_synced:
            return "positions_not_synced"
        if not risk_ok:
            return "risk_not_ready"
        if ready:
            return "ready"
        return "unknown"

    def _normalize_mode(self, mode: Any) -> str:
        return "live" if str(mode or "").strip().lower() == "live" else "paper"