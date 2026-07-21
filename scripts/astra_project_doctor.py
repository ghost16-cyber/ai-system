#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.operations import collect_project_runtime_diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Astra canonical project diagnostics.")
    parser.add_argument("--database-path", default="data/app/ai_system.db")
    parser.add_argument("--no-docker", action="store_true", help="Skip Docker image/digest inspection.")
    args = parser.parse_args()
    report = collect_project_runtime_diagnostics(
        Path(args.database_path), check_docker=not args.no_docker,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["safe_to_start"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
