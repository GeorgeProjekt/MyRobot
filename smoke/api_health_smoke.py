#!/usr/bin/env python3
"""Minimal API health smoke: start uvicorn, call /api/health, capture artefacts."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PORT = 8123


def _find_free_port(preferred: int = DEFAULT_PORT) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]


def api_health_smoke(artifact_root: Path) -> dict:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_root / f"api_health_smoke_{timestamp}.json"
    log_path = artifact_root / f"api_health_smoke_{timestamp}.log"

    port = _find_free_port()
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.api.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]

    proc: Optional[subprocess.Popen] = None
    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
        client = httpx.Client(timeout=5.0)
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20.0
        response: Optional[httpx.Response] = None
        while time.time() < deadline:
            try:
                response = client.get(f"{base_url}/api/health")
                break
            except Exception:
                time.sleep(0.5)
        client.close()

        if response is None:
            raise RuntimeError("API health endpoint did not respond in time")

        summary = {
            "status": "API_HEALTH_PASS" if response.status_code == 200 else "API_HEALTH_FAIL",
            "timestamp": timestamp,
            "url": f"{base_url}/api/health",
            "status_code": response.status_code,
            "payload": response.text,
            "artifact": str(artifact_path),
            "log": str(log_path),
        }
        artifact_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if summary["status"] != "API_HEALTH_PASS":
            raise RuntimeError(f"Unexpected status code {response.status_code}")
        return summary
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def main() -> int:
    artifact_dir = Path("runtime")
    try:
        summary = api_health_smoke(artifact_dir)
    except Exception as exc:
        error_path = artifact_dir / "api_health_smoke_error.log"
        error_path.write_text(str(exc) + "\n", encoding="utf-8")
        print("API_HEALTH_SMOKE_FAIL", json.dumps({"error": str(exc), "log": str(error_path)}, ensure_ascii=False))
        return 1
    print("API_HEALTH_SMOKE_PASS", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
