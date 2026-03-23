from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()  # type: ignore[attr-defined]
        except Exception:
            return str(value)
    if hasattr(value, "dict"):
        try:
            return value.dict()  # type: ignore[attr-defined]
        except Exception:
            return str(value)
    if hasattr(value, "__dict__"):
        try:
            return {str(k): _jsonable(v) for k, v in vars(value).items()}
        except Exception:
            return str(value)
    return str(value)


class TelemetryService:
    """Minimal file-based telemetry sink used by RobotService."""

    def __init__(self, pair: Optional[str] = None, *, path: Optional[str] = None) -> None:
        self.pair = str(pair or "").upper().strip()
        default_path = Path("runtime") / "telemetry" / "events.jsonl"
        self.path = Path(path).resolve() if path else default_path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def emit(self, event: str, payload: Any = None, **extra: Any) -> None:
        record: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "pair": self.pair or None,
            "event": str(event or "").strip(),
            "payload": _jsonable(payload),
            "extra": _jsonable(extra) if extra else None,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        if limit <= 0 or not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
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
