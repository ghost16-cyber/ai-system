#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.database.migrations import (  # noqa: E402
    LATEST_SCHEMA_VERSION,
    apply_schema_migrations,
    preflight_schema_compatibility,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply reviewed additive Astra database migrations."
    )
    parser.add_argument("--database-path", default="data/app/ai_system.db")
    args = parser.parse_args()
    path = Path(args.database_path).expanduser().resolve()
    before = preflight_schema_compatibility(path)
    result = apply_schema_migrations(path)
    backup = path.with_name(f"{path.name}.pre-stage3h-v8.bak")
    print(
        json.dumps(
            {
                "schema_version": "astra.database-migration.result.v1",
                "database_path": str(path),
                "previous_version": before,
                "current_version": result.current_version,
                "latest_version": LATEST_SCHEMA_VERSION,
                "applied_versions": list(result.applied_versions),
                "stage3h_backup_path": str(backup) if backup.is_file() else None,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
