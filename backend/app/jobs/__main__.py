from __future__ import annotations

import os
from pathlib import Path

from .queue import JobQueue
from .worker import LocalWorker


DEFAULT_DATABASE_PATH = Path("data/app/ai_system.db")


def main() -> None:
    database_path = os.getenv("AI_SYSTEM_DB_PATH", str(DEFAULT_DATABASE_PATH))
    queue = JobQueue(database_path)
    queue.initialize()
    worker = LocalWorker(queue, handlers={})
    worker.run_forever()


if __name__ == "__main__":
    main()
