from __future__ import annotations

import os
import time
from pathlib import Path
from contextlib import contextmanager

LOCK_PATH = Path("run") / "trading_step.lock"
LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

@contextmanager
def trading_step_lock(timeout_sec: float = 60.0, poll_sec: float = 0.1):
    start = time.time()
    f = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except Exception:
                if (time.time() - start) >= timeout_sec:
                    raise TimeoutError("Trading step lock timeout (another run_once is still running).")
                time.sleep(poll_sec)

        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            f.close()
        except Exception:
            pass
