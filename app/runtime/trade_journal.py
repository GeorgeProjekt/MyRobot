from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_JOURNAL_SINGLETON: Optional["TradeJournal"] = None
_JOURNAL_LOCK = threading.RLock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_jsonable(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {str(k): _safe_jsonable(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_safe_jsonable(v) for v in value]

    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass

    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:
            pass

    if hasattr(value, "__dict__"):
        try:
            return {str(k): _safe_jsonable(v) for k, v in vars(value).items()}
        except Exception:
            pass

    return str(value)


class TradeJournal:
    """
    Thread-safe JSONL journal for runtime audit.

    Journal files:
    - trades.jsonl
    - decisions.jsonl
    - risk.jsonl

    Each line is one JSON object with UTC timestamp.
    """

    def __init__(self, base_dir: Optional[str | Path] = None) -> None:
        if base_dir is None:
            base_dir = Path("runtime") / "journal"

        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()

        self.trades_file = self.base_dir / "trades.jsonl"
        self.decisions_file = self.base_dir / "decisions.jsonl"
        self.risk_file = self.base_dir / "risk.jsonl"

    # ---------------------------------------------------------
    # LOW LEVEL
    # ---------------------------------------------------------

    def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _read_jsonl_tail(self, path: Path, limit: int = 100) -> List[Dict[str, Any]]:
        if limit <= 0 or not path.exists():
            return []

        with self._lock:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

        out: List[Dict[str, Any]] = []
        for raw in lines[-limit:]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    # ---------------------------------------------------------
    # PUBLIC WRITE API
    # ---------------------------------------------------------

    def log_trade(
        self,
        *,
        pair: str,
        side: str,
        price: float,
        amount: float,
        mode: str,
        pnl: Optional[float] = None,
        order_id: Optional[str] = None,
        status: Optional[str] = None,
        exchange: Optional[str] = None,
        execution_ok: Optional[bool] = None,
        origin: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "ts": _utc_now_iso(),
            "pair": str(pair).upper().strip(),
            "side": str(side).upper().strip(),
            "price": float(price),
            "amount": float(amount),
            "mode": str(mode).lower().strip(),
            "pnl": float(pnl) if pnl is not None else None,
            "order_id": str(order_id) if order_id not in (None, "") else None,
            "status": str(status) if status not in (None, "") else None,
            "exchange": str(exchange) if exchange not in (None, "") else None,
            "execution_ok": bool(execution_ok) if execution_ok is not None else None,
            "origin": str(origin) if origin not in (None, "") else None,
        }

        if extra:
            payload["extra"] = _safe_jsonable(extra)

        self._append_jsonl(self.trades_file, payload)

    def log_decision(
        self,
        *,
        pair: str,
        decision: Dict[str, Any],
        analysis: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "ts": _utc_now_iso(),
            "pair": str(pair).upper().strip(),
            "decision": _safe_jsonable(decision),
            "analysis": _safe_jsonable(analysis) if analysis is not None else None,
        }
        self._append_jsonl(self.decisions_file, payload)

    def log_risk(
        self,
        *,
        pair: str,
        risk_diag: Dict[str, Any],
        decision: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "ts": _utc_now_iso(),
            "pair": str(pair).upper().strip(),
            "risk_diag": _safe_jsonable(risk_diag),
            "decision": _safe_jsonable(decision) if decision is not None else None,
        }
        self._append_jsonl(self.risk_file, payload)

    # ---------------------------------------------------------
    # PUBLIC READ API
    # ---------------------------------------------------------

    def recent_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._read_jsonl_tail(self.trades_file, limit=limit)

    def recent_decisions(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._read_jsonl_tail(self.decisions_file, limit=limit)

    def recent_risk(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._read_jsonl_tail(self.risk_file, limit=limit)


def get_trade_journal(base_dir: Optional[str | Path] = None) -> TradeJournal:
    global _JOURNAL_SINGLETON

    with _JOURNAL_LOCK:
        if _JOURNAL_SINGLETON is None:
            _JOURNAL_SINGLETON = TradeJournal(base_dir=base_dir)
        return _JOURNAL_SINGLETON