from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Dict, Optional


@dataclass
class ExposureSnapshot:
    pair: str
    gross_exposure: float
    net_exposure: float
    positions: Dict[str, float]
    max_total_exposure: float
    updated_ts: float


@dataclass
class _State:
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    positions: Dict[str, float] = field(default_factory=dict)
    updated_ts: float = 0.0


class ExposureManager:
    """
    Persistent pair-isolated exposure manager.

    Rules:
    - exposure is tracked in normalized size units or notional units chosen by caller
    - gross exposure = sum(abs(position))
    - net exposure = algebraic sum(position)
    - register_open adds to position
    - register_close reduces position
    """

    def __init__(
        self,
        max_total_exposure: float = 1.0,
        *,
        pair: Optional[str] = None,
        state_dir: Optional[str | Path] = None,
    ) -> None:
        self.pair = str(pair or "GLOBAL").upper().strip()
        self.max_total_exposure = max(float(max_total_exposure), 0.0)

        self._lock = RLock()
        self._state = _State()

        base_dir = Path(state_dir or (Path("runtime") / "exposure")).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = base_dir / f"{self.pair.lower()}_exposure.json"

        self._load()

    # ---------------------------------------------------------

    def can_open(self, size: float) -> bool:
        with self._lock:
            proposed = abs(float(size or 0.0))
            return (self._state.gross_exposure + proposed) <= self.max_total_exposure

    def register(self, size: float, symbol: Optional[str] = None) -> None:
        side = "BUY" if float(size or 0.0) >= 0 else "SELL"
        self.register_open(symbol=symbol or self.pair, size=abs(float(size or 0.0)), side=side)

    def reduce(self, size: float, symbol: Optional[str] = None) -> None:
        self.register_close(symbol=symbol or self.pair, size=abs(float(size or 0.0)))

    # ---------------------------------------------------------

    def register_open(self, *, symbol: str, size: float, side: str = "BUY") -> None:
        with self._lock:
            sym = str(symbol or self.pair).upper().strip()
            qty = abs(float(size or 0.0))
            if qty <= 0:
                return

            signed = qty if str(side).upper().strip() == "BUY" else -qty
            current = float(self._state.positions.get(sym, 0.0))
            self._state.positions[sym] = current + signed

            self._recompute()
            self._save()

    def register_close(self, *, symbol: str, size: float) -> None:
        with self._lock:
            sym = str(symbol or self.pair).upper().strip()
            qty = abs(float(size or 0.0))
            if qty <= 0:
                return

            current = float(self._state.positions.get(sym, 0.0))
            if current > 0:
                current = max(0.0, current - qty)
            elif current < 0:
                current = min(0.0, current + qty)

            if abs(current) <= 1e-12:
                self._state.positions.pop(sym, None)
            else:
                self._state.positions[sym] = current

            self._recompute()
            self._save()

    def set_position(self, *, symbol: str, signed_size: float) -> None:
        with self._lock:
            sym = str(symbol or self.pair).upper().strip()
            value = float(signed_size or 0.0)

            if abs(value) <= 1e-12:
                self._state.positions.pop(sym, None)
            else:
                self._state.positions[sym] = value

            self._recompute()
            self._save()

    def reset(self) -> None:
        with self._lock:
            self._state = _State(updated_ts=time.time())
            self._save()

    # ---------------------------------------------------------

    @property
    def current_exposure(self) -> float:
        with self._lock:
            return float(self._state.gross_exposure)

    def snapshot(self) -> ExposureSnapshot:
        with self._lock:
            return ExposureSnapshot(
                pair=self.pair,
                gross_exposure=float(self._state.gross_exposure),
                net_exposure=float(self._state.net_exposure),
                positions=dict(self._state.positions),
                max_total_exposure=float(self.max_total_exposure),
                updated_ts=float(self._state.updated_ts or 0.0),
            )

    # ---------------------------------------------------------
    # INTERNALS
    # ---------------------------------------------------------

    def _recompute(self) -> None:
        gross = 0.0
        net = 0.0
        for value in self._state.positions.values():
            v = float(value or 0.0)
            gross += abs(v)
            net += v

        self._state.gross_exposure = float(gross)
        self._state.net_exposure = float(net)
        self._state.updated_ts = time.time()

    def _load(self) -> None:
        with self._lock:
            if not self._state_file.exists():
                return

            try:
                payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            except Exception:
                return

            state = payload.get("state")
            if not isinstance(state, dict):
                return

            try:
                positions_raw = state.get("positions") or {}
                positions = {
                    str(symbol).upper(): float(value or 0.0)
                    for symbol, value in positions_raw.items()
                    if isinstance(positions_raw, dict)
                }

                self._state = _State(
                    gross_exposure=float(state.get("gross_exposure") or 0.0),
                    net_exposure=float(state.get("net_exposure") or 0.0),
                    positions=positions,
                    updated_ts=float(state.get("updated_ts") or 0.0),
                )
                self._recompute()
            except Exception:
                self._state = _State()

    def _save(self) -> None:
        with self._lock:
            payload = {
                "pair": self.pair,
                "updated_ts": time.time(),
                "max_total_exposure": self.max_total_exposure,
                "state": asdict(self._state),
            }
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )