from __future__ import annotations

import os
from pathlib import Path

from .handlers import build_job_handlers
from .queue import JobQueue
from .worker import LocalWorker


DEFAULT_DATABASE_PATH = Path("data/app/ai_system.db")
DEFAULT_WORKSPACE_ROOT = Path.cwd()


def main() -> None:
    database_path = os.getenv("AI_SYSTEM_DB_PATH", str(DEFAULT_DATABASE_PATH))
    workspace_root = os.getenv("AI_SYSTEM_WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT))
    queue = JobQueue(database_path)
    queue.initialize()
    worker = LocalWorker(queue, handlers=build_job_handlers(workspace_root))
    worker.run_forever()


if __name__ == "__main__":
    main()
