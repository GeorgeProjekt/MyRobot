#!/usr/bin/env python3
"""Telemetry + alert smoke for paper/offline baseline."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

TELEMETRY_PATH = Path("runtime/telemetry/events.jsonl")
ALERT_DIR = Path("runtime/alerts")


def _load_telemetry(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Telemetry file missing: {path}")
    entries = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not entries:
        raise RuntimeError("Telemetry file exists but contains no parsable events")
    last_entry = entries[-1]
    last_ts = last_entry.get("ts")
    return {
        "entry_count": len(entries),
        "last_entry": last_entry,
        "last_timestamp": last_ts,
    }


def telemetry_smoke() -> Dict[str, Any]:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    telemetry_info = _load_telemetry(TELEMETRY_PATH)
    summary = {
        "status": "TELEMETRY_SMOKE_PASS",
        "timestamp": timestamp,
        "telemetry_path": str(TELEMETRY_PATH),
        "entry_count": telemetry_info["entry_count"],
        "last_timestamp": telemetry_info["last_timestamp"],
        "last_entry": telemetry_info["last_entry"],
    }
    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    artefact_path = ALERT_DIR / f"telemetry_smoke_{timestamp}.json"
    last_alert_path = ALERT_DIR / "last_alert.json"
    artefact_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    last_alert_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary["artefact"] = str(artefact_path)
    summary["last_alert"] = str(last_alert_path)
    return summary


def main() -> int:
    try:
        summary = telemetry_smoke()
    except Exception as exc:
        payload = {"status": "TELEMETRY_SMOKE_FAIL", "error": str(exc), "telemetry_path": str(TELEMETRY_PATH)}
        print("TELEMETRY_SMOKE_FAIL", json.dumps(payload, ensure_ascii=False))
        return 1
    print("TELEMETRY_SMOKE_PASS", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
