#!/usr/bin/env python3
"""Health & telemetry snapshot helper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"


def _read_last_json_line(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return None
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def journal_snapshot(base: Path) -> Dict[str, Any]:
    trades = _read_last_json_line(base / "trades.jsonl")
    decisions = _read_last_json_line(base / "decisions.jsonl")
    risk = _read_last_json_line(base / "risk.jsonl")
    status = "OK" if trades or decisions or risk else "UNKNOWN"
    last_ts = None
    pair = None
    last_error = None
    for entry in filter(None, (trades, decisions, risk)):
        if entry.get("ts"):
            last_ts = entry.get("ts")
        if entry.get("pair"):
            pair = entry.get("pair")
        if entry.get("status") and str(entry.get("status")).lower() in {"error", "failed"}:
            status = "ERR"
            last_error = entry.get("status")
    return {
        "source": str(base),
        "status": status,
        "pair": pair,
        "ts": last_ts,
        "last_error": last_error,
    }


def watchdog_snapshot(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "source": str(path),
            "status": "UNKNOWN",
            "pairs": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "source": str(path),
            "status": "ERR",
            "pairs": {},
            "last_error": "invalid_json",
        }
    return {
        "source": str(path),
        "status": "OK" if payload else "UNKNOWN",
        "pairs": payload,
    }


def telemetry_snapshot(path: Path) -> Dict[str, Any]:
    last = _read_last_json_line(path)
    if last is None:
        return {
            "source": str(path),
            "status": "UNKNOWN",
        }
    return {
        "source": str(path),
        "status": "OK",
        "event": last.get("event"),
        "pair": last.get("pair"),
        "ts": last.get("ts"),
    }


def build_snapshot() -> Dict[str, Any]:
    journal_dir = RUNTIME / "journal"
    watchdog_path = RUNTIME / "watchdog" / "pairs.json"
    telemetry_path = RUNTIME / "telemetry" / "events.jsonl"
    return {
        "journal": journal_snapshot(journal_dir),
        "watchdog": watchdog_snapshot(watchdog_path),
        "telemetry": telemetry_snapshot(telemetry_path),
    }


def main() -> int:
    snapshot = build_snapshot()
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
