#!/usr/bin/env python3
"""Fail-safe evaluator based on health snapshot."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import health_snapshot

FAILSAFE_PATH = ROOT / "runtime" / "watchdog" / "failsafe.json"


def evaluate(snapshot):
    reasons = []
    for key in ("journal", "watchdog", "telemetry"):
        section = snapshot.get(key, {})
        status = section.get("status", "UNKNOWN")
        if status != "OK":
            reasons.append({"component": key, "status": status})
    return {
        "status": "ABORT" if reasons else "PASS",
        "reasons": reasons,
        "snapshot": snapshot,
    }


def write_artifact(payload):
    FAILSAFE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FAILSAFE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    snapshot = health_snapshot.build_snapshot()
    payload = evaluate(snapshot)
    write_artifact(payload)
    print(json.dumps({"status": payload["status"], "reasons": payload["reasons"]}, ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
