from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, Any


@dataclass
class SchedulerConfig:
    enabled: bool
    hour: int
    minute: int
    last_run_ts: Optional[int]
    last_run_ok: bool
    last_error: str
    mode: str  # "paper" | "live"


class SimpleDailyScheduler:
    """
    Minimal dependency scheduler:
    - runs once per day at configured (hour:minute) local time
    - avoids duplicate start using a lock file (important for uvicorn --reload)
    """

    def __init__(
        self,
        *,
        lock_path: Path,
        load_cfg: Callable[[], SchedulerConfig],
        save_last_run: Callable[[bool, str], None],
        job: Callable[[], None],
        poll_sec: int = 1,
    ) -> None:
        self.lock_path = lock_path
        self.load_cfg = load_cfg
        self.save_last_run = save_last_run
        self.job = job
        self.poll_sec = max(int(poll_sec), 1)
        self._stop = False
        self._lock_fd: Optional[int] = None

    def acquire_lock(self) -> bool:
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            # atomic create exclusive
            self._lock_fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._lock_fd, str(os.getpid()).encode("utf-8"))
            return True
        except FileExistsError:
            return False
        except Exception:
            return False

    def release_lock(self) -> None:
        try:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            if self.lock_path.exists():
                self.lock_path.unlink()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop = True

    def run_forever(self) -> None:
        if not self.acquire_lock():
            # another process/thread already runs scheduler
            return

        try:
            while not self._stop:
                cfg = self.load_cfg()
                if cfg.enabled:
                    now = datetime.now()
                    target = now.replace(hour=cfg.hour, minute=cfg.minute, second=0, microsecond=0)
                    now_ts = int(now.timestamp())
                    target_ts = int(target.timestamp())

                    # determine if we should run (once per day)
                    last_ts = cfg.last_run_ts or 0

                    # run when we are past target time and last run was before today's target
                    if now_ts >= target_ts and last_ts < target_ts:
                        try:
                            self.job()
                            self.save_last_run(True, "")
                        except Exception as e:
                            self.save_last_run(False, str(e))

                time.sleep(self.poll_sec)
        finally:
            self.release_lock()
