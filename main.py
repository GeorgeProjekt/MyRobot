print("IMPORTED main.py")

from app.env_bootstrap import load_project_env

load_project_env()

from app.storage import init_db

init_db()

from app.api.app import app

__all__ = ["app"]
