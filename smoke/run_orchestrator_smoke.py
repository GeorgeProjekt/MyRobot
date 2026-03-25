#!/usr/bin/env python3
"""Single-pair orchestrator smoke harness (paper-only scope)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smoke.run_smoke import _patched_runtime_journal, _build_stub_service
from app.orchestrator.global_orchestrator import GlobalOrchestrator


async def _run_single_pair(pair: str, duration: float, artifact_root: Path) -> dict:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    artifact_root.mkdir(parents=True, exist_ok=True)

    journal_dir = artifact_root / f"orchestrator_smoke_journal_{timestamp}"
    journal_dir.mkdir(parents=True, exist_ok=True)
    _patched_runtime_journal(journal_dir)

    orchestrator = GlobalOrchestrator()
    orchestrator.attach_service(pair, _build_stub_service(pair=pair))
    await orchestrator.start()
    await asyncio.sleep(max(0.5, duration))
    snapshot = orchestrator.snapshot()
    events = orchestrator.get_event_log()
    await orchestrator.stop()

    effective_statuses = {
        p: data.get("metadata", {}).get("effective_status") for p, data in snapshot.items()
    }

    summary = {
        "status": "ORCH_SMOKE_PASS",
        "timestamp": timestamp,
        "pair": pair,
        "duration_sec": duration,
        "journal_dir": str(journal_dir),
        "effective_statuses": effective_statuses,
        "event_sample": events[-5:] if len(events) > 5 else events,
    }

    artifact_path = artifact_root / f"orchestrator_smoke_{timestamp}.json"
    summary["artifact"] = str(artifact_path)
    artifact_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


async def run_smoke(pairs: list[str], duration: float, artifact_root: Path) -> list[dict]:
    results: list[dict] = []
    for pair in pairs:
        results.append(await _run_single_pair(pair=pair, duration=duration, artifact_root=artifact_root))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Single-pair orchestrator smoke harness (paper-only).")
    parser.add_argument("--pair", default="BTC_EUR", help="Pair to run when --pairs is omitted")
    parser.add_argument("--pairs", nargs="+", help="Optional list of pairs to run sequentially (paper-only).")
    parser.add_argument("--duration", type=float, default=3.0, help="How long to let each orchestrator loop run in seconds")
    parser.add_argument(
        "--artifact-dir",
        default="runtime",
        help="Directory for storing smoke artefacts (JSON + journal folder).",
    )
    args = parser.parse_args()

    artifact_root = Path(args.artifact_dir)
    pairs = args.pairs if args.pairs else [args.pair]

    try:
        results = asyncio.run(run_smoke(pairs=pairs, duration=args.duration, artifact_root=artifact_root))
    except KeyboardInterrupt:
        print("ORCH_SMOKE_ABORT", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - smoke harness diagnostics only
        print("ORCH_SMOKE_FAIL", json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    summary = {
        "status": "ORCH_SMOKE_PASS",
        "pairs": results,
    }
    print("ORCH_SMOKE_PASS", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
