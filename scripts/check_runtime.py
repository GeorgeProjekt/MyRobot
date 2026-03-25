#!/usr/bin/env python3
"""Minimal runtime/dependency probe for paper-ready environment."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_MODULES = [
    "fastapi",
    "uvicorn",
    "httpx",
    "requests",
    "dotenv",
    "pydantic",
    "numpy",
    "pandas",
]
INTERNAL_MODULES = [
    "app.services.robot_service",
    "scripts.paper_run",
    "scripts.health_snapshot",
]


def probe_module(name: str) -> Dict[str, str]:
    try:
        importlib.import_module(name)
        return {"module": name, "status": "OK"}
    except Exception as exc:
        return {"module": name, "status": "FAIL", "reason": str(exc)}


def main() -> int:
    results = {
        "required": [probe_module(m) for m in REQUIRED_MODULES],
        "internal": [probe_module(m) for m in INTERNAL_MODULES],
    }
    print(json.dumps(results, indent=2, ensure_ascii=False))
    failures = [entry for section in results.values() for entry in section if entry["status"] != "OK"]
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
