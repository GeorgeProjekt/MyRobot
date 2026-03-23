# app/storage/db.py

import sqlite3
import threading
from pathlib import Path
from config import DB_PATH


_db_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:

    Path(DB_PATH).touch(exist_ok=True)

    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=30,
        isolation_level=None,
    )

    conn.row_factory = sqlite3.Row

    return conn


def _ensure_kv_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv (
            k TEXT PRIMARY KEY,
            v TEXT
        )
        """
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> bool:
    columns = _table_columns(conn, table_name)
    if column_name in columns:
        return False
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
    return True


def _ensure_decisions_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            ts TEXT,
            run_id TEXT,
            symbol TEXT,
            timeframe TEXT,
            action TEXT,
            reason TEXT,
            decision_inputs_json TEXT,
            indicators_json TEXT,
            rejected_json TEXT,
            risk REAL,
            fgi INTEGER
        )
        """
    )

    _add_column_if_missing(conn, "decisions", "risk_pct", "REAL")

    columns = _table_columns(conn, "decisions")

    if "risk" not in columns:
        conn.execute("ALTER TABLE decisions ADD COLUMN risk REAL")
        columns.add("risk")

    if "risk_pct" in columns and "risk" in columns:
        conn.execute(
            """
            UPDATE decisions
               SET risk_pct = COALESCE(risk_pct, risk),
                   risk = COALESCE(risk, risk_pct)
             WHERE risk_pct IS NULL OR risk IS NULL
            """
        )


def init_db() -> None:

    with _db_lock:

        conn = get_conn()
        cur = conn.cursor()

        try:

            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")

            _ensure_kv_table(conn)

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    ts TEXT,
                    timeframe TEXT,
                    fgi INTEGER,
                    risk REAL,
                    run_id TEXT,
                    ok INTEGER,
                    error TEXT
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS backtests (
                    ts TEXT,
                    symbol TEXT,
                    timeframe TEXT,
                    days INTEGER,
                    roi REAL,
                    sharpe REAL,
                    maxdd REAL,
                    trades INTEGER
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_backtests (
                    ts TEXT,
                    timeframe TEXT,
                    days INTEGER,
                    roi REAL,
                    sharpe REAL,
                    maxdd REAL,
                    trades INTEGER
                )
                """
            )

            _ensure_decisions_schema(conn)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol)")

            conn.commit()

        finally:

            conn.close()


def kv_get(key: str, default: str | None = None) -> str | None:

    with _db_lock:

        conn = get_conn()

        try:

            _ensure_kv_table(conn)

            row = conn.execute(
                "SELECT v FROM kv WHERE k=?",
                (key,),
            ).fetchone()

            if row is None:
                return default

            return row["v"]

        finally:

            conn.close()


def kv_set(key: str, value: str) -> None:

    with _db_lock:

        conn = get_conn()

        try:

            _ensure_kv_table(conn)

            conn.execute(
                """
                INSERT INTO kv(k,v)
                VALUES(?,?)
                ON CONFLICT(k) DO UPDATE SET v=excluded.v
                """,
                (key, value),
            )

            conn.commit()

        finally:

            conn.close()


def kv_delete(key: str) -> None:

    with _db_lock:

        conn = get_conn()

        try:

            _ensure_kv_table(conn)

            conn.execute(
                "DELETE FROM kv WHERE k=?",
                (key,),
            )

            conn.commit()

        finally:

            conn.close()


def kv_all() -> dict:

    with _db_lock:

        conn = get_conn()

        try:

            _ensure_kv_table(conn)

            rows = conn.execute(
                "SELECT k,v FROM kv"
            ).fetchall()

            return {row["k"]: row["v"] for row in rows}

        finally:

            conn.close()
