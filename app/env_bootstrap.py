from __future__ import annotations

from pathlib import Path


_BOOTSTRAPPED = False


def _project_root() -> Path:
    # env_bootstrap.py lives in <project_root>/app/env_bootstrap.py
    # so project root is parents[1], not parents[2]
    return Path(__file__).resolve().parents[1]


def load_project_env() -> Path | None:
    """
    Load environment variables from the project `.env` file exactly once.

    Rules:
    - `.env` is expected in the project root (next to `main.py` / `server.py`)
    - existing OS environment variables keep priority
    - missing `python-dotenv` does not crash startup
    """
    global _BOOTSTRAPPED
    env_path = _project_root() / ".env"

    if _BOOTSTRAPPED:
        return env_path if env_path.exists() else None

    _BOOTSTRAPPED = True

    if not env_path.exists():
        return None

    try:
        from dotenv import load_dotenv
    except Exception:
        return env_path

    load_dotenv(dotenv_path=env_path, override=False)
    return env_path


__all__ = ["load_project_env"]
