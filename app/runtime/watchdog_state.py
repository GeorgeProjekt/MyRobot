from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

_STATE_PATH = (Path("runtime") / "watchdog" / "pairs.json").resolve()
_LOCK = threading.RLock()


def record_pair_health(pair: str, payload: Dict[str, Any]) -> None:
    pair_name = str(pair or "").upper().strip()
    if not pair_name:
        return
    snapshot = dict(payload or {})
    snapshot["pair"] = pair_name
    snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with _LOCK:
        state: Dict[str, Any] = {}
        if _STATE_PATH.exists():
            try:
                state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        state[pair_name] = snapshot
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_STATE_PATH)
