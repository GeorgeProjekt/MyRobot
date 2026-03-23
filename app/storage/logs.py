from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from app.storage import db


_storage_bootstrap_lock = threading.Lock()
_storage_bootstrapped = False


def _ensure_storage_ready() -> None:
    global _storage_bootstrapped

    if _storage_bootstrapped:
        return

    with _storage_bootstrap_lock:
        if _storage_bootstrapped:
            return
        db.init_db()
        _storage_bootstrapped = True


def _now_iso() -> str:
    # ISO8601 without timezone confusion (local time) - adequate for local logs
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def new_run_id() -> str:
    # short-ish but unique enough; keep readable
    return uuid.uuid4().hex[:12]


def _log_path() -> Path:
    # Allow override; default inside project root if possible.
    p = os.environ.get("MYROBOT_LOG_PATH", "")
    if p.strip():
        return Path(p).expanduser().resolve()
    return Path("logs/trading.jsonl").resolve()


def log_event(event: str, **fields: Any) -> None:
    """Write one structured JSON log line. Never raises."""
    try:
        rec: Dict[str, Any] = {"ts": _now_iso(), "event": event}
        rec.update(fields)

        lp = _log_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # best-effort, do not crash trading loop
        pass


def record_run(run_id: str, timeframe: str, fgi: int, risk_pct: float, ok: bool, error: str | None = None) -> None:
    """Persist summary of the run into DB (runs table)."""
    try:
        _ensure_storage_ready()
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO runs(ts, timeframe, fgi, risk, run_id, ok, error) VALUES(datetime('now'), ?, ?, ?, ?, ?, ?)",
            (timeframe, int(fgi), float(risk_pct), str(run_id), 1 if ok else 0, (error or "")),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log_event("db_error", where="record_run", err=str(e), run_id=run_id)


def record_decision(
    *,
    run_id: str,
    symbol: str,
    timeframe: str,
    action: str,
    reason: str,
    decision_inputs: Dict[str, Any],
    indicators: Dict[str, Any],
    rejected_reasons: List[str],
    risk_pct: float,
    fgi: int,
) -> None:
    """Persist one symbol-level decision into DB and JSONL."""
    # JSONL first (so even if DB is down, you have a trail)
    log_event(
        "decision",
        run_id=run_id,
        symbol=symbol,
        timeframe=timeframe,
        action=action,
        reason=reason,
        risk_pct=float(risk_pct),
        fgi=int(fgi),
        decision_inputs=decision_inputs,
        indicators=indicators,
        rejected=rejected_reasons,
    )

    try:
        _ensure_storage_ready()
        conn = db.get_conn()
        conn.execute(
            """
            INSERT INTO decisions(
              ts, run_id, symbol, timeframe, action, reason,
              decision_inputs_json, indicators_json, rejected_json,
              risk_pct, risk, fgi
            ) VALUES(datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run_id),
                str(symbol),
                str(timeframe),
                str(action),
                str(reason),
                json.dumps(decision_inputs, ensure_ascii=False),
                json.dumps(indicators, ensure_ascii=False),
                json.dumps(rejected_reasons, ensure_ascii=False),
                float(risk_pct),
                float(risk_pct),
                int(fgi),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log_event("db_error", where="record_decision", err=str(e), run_id=run_id, symbol=symbol)


def fetch_latest_decisions(limit: int = 200) -> List[Dict[str, Any]]:
    """Convenience reader for UI/API."""
    try:
        _ensure_storage_ready()
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT ts, run_id, symbol, timeframe, action, reason, decision_inputs_json, indicators_json, "
            "rejected_json, COALESCE(risk_pct, risk) AS risk_pct, fgi "
            "FROM decisions ORDER BY ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        conn.close()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "ts": r["ts"],
                    "run_id": r["run_id"],
                    "symbol": r["symbol"],
                    "timeframe": r["timeframe"],
                    "action": r["action"],
                    "reason": r["reason"],
                    "risk_pct": r["risk_pct"],
                    "fgi": r["fgi"],
                    "decision_inputs": json.loads(r["decision_inputs_json"] or "{}"),
                    "indicators": json.loads(r["indicators_json"] or "{}"),
                    "rejected": json.loads(r["rejected_json"] or "[]"),
                }
            )
        return out
    except Exception as e:
        log_event("db_error", where="fetch_latest_decisions", err=str(e))
        return []
