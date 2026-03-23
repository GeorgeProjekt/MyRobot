from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

DB_PATH = Path("trading_state.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def ensure_audit_table() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              event TEXT NOT NULL,
              data_json TEXT NOT NULL
            )
            """
        )
        c.commit()


def log_event(event: str, data: Dict[str, Any] | None = None) -> None:
    ensure_audit_table()
    payload = data or {}
    with _conn() as c:
        c.execute(
            "INSERT INTO audit_log(ts, event, data_json) VALUES (?, ?, ?)",
            (int(time.time()), str(event), json.dumps(payload, ensure_ascii=False)),
        )
        c.commit()


def recent(limit: int = 50) -> List[Dict[str, Any]]:
    ensure_audit_table()
    lim = max(1, min(int(limit), 500))
    with _conn() as c:
        rows = c.execute(
            "SELECT id, ts, event, data_json FROM audit_log ORDER BY id DESC LIMIT ?",
            (lim,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            data = json.loads(r["data_json"])
        except Exception:
            data = {"raw": r["data_json"]}
        out.append({"id": r["id"], "ts": r["ts"], "event": r["event"], "data": data})
    return out
