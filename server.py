print("STARTED FROM server.py")

import uvicorn

from app.env_bootstrap import load_project_env

load_project_env()

from app.storage import init_db

init_db()

if __name__ == "__main__":
    uvicorn.run(
        "app.api.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
