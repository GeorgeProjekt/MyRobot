from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class HealthSnapshot:
    status: str
    market_data_ok: bool
    execution_ok: bool
    risk_ok: bool
    control_ok: bool
    last_error: Optional[str]
    updated_ts: float


class HealthMonitor:
    """
    Deterministic runtime health monitor.

    Purpose:
    - aggregate subsystem health into one truthful status
    - avoid placeholder booleans with no context
    - expose stable snapshot for dashboard / orchestration
    """

    def __init__(self) -> None:
        self._snapshot = HealthSnapshot(
            status="UNKNOWN",
            market_data_ok=False,
            execution_ok=False,
            risk_ok=False,
            control_ok=False,
            last_error=None,
            updated_ts=time.time(),
        )

    def update(
        self,
        *,
        market_data_ok: bool,
        execution_ok: bool,
        risk_ok: bool,
        control_ok: bool,
        last_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        status = self._compute_status(
            market_data_ok=market_data_ok,
            execution_ok=execution_ok,
            risk_ok=risk_ok,
            control_ok=control_ok,
            last_error=last_error,
        )

        self._snapshot = HealthSnapshot(
            status=status,
            market_data_ok=bool(market_data_ok),
            execution_ok=bool(execution_ok),
            risk_ok=bool(risk_ok),
            control_ok=bool(control_ok),
            last_error=(str(last_error) if last_error not in (None, "") else None),
            updated_ts=time.time(),
        )
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self._snapshot)

    def _compute_status(
        self,
        *,
        market_data_ok: bool,
        execution_ok: bool,
        risk_ok: bool,
        control_ok: bool,
        last_error: Optional[str],
    ) -> str:
        if last_error:
            return "ERROR"

        if not control_ok:
            return "BLOCKED"

        if not market_data_ok:
            return "DEGRADED"

        if not risk_ok:
            return "RISK_BLOCKED"

        if not execution_ok:
            return "EXECUTION_DEGRADED"

        return "OK"