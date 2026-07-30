from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_file_checkpoint(
    *,
    checkpoint_root: str | Path,
    task_id: str,
    project_root: Path,
    relative_path: str,
    patch: dict[str, object],
) -> dict[str, object]:
    target = (project_root / relative_path).resolve()
    original = target.read_text(encoding="utf-8")
    checkpoint_id = str(uuid4())
    checkpoint_dir = Path(checkpoint_root) / task_id
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"{checkpoint_id}.json"
    data = {
        "checkpoint_id": checkpoint_id,
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_root": project_root.resolve().as_posix(),
        "relative_path": relative_path,
        "original_content": original,
        "original_sha256": sha256_text(original),
        "patch": patch,
    }
    checkpoint_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "checkpoint_id": checkpoint_id,
        "checkpoint_path": checkpoint_path.as_posix(),
        "relative_path": relative_path,
        "original_sha256": data["original_sha256"],
    }


def rollback_checkpoint(checkpoint_path: str | Path) -> dict[str, object]:
    path = Path(checkpoint_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    project_root = Path(str(data["project_root"])).resolve()
    target = (project_root / str(data["relative_path"])).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    original = str(data["original_content"])
    target.write_text(original, encoding="utf-8")
    restored_sha = sha256_text(target.read_text(encoding="utf-8"))
    expected_sha = str(data["original_sha256"])
    return {
        "checkpoint_id": data["checkpoint_id"],
        "checkpoint_path": path.as_posix(),
        "relative_path": data["relative_path"],
        "restored": restored_sha == expected_sha,
        "expected_sha256": expected_sha,
        "restored_sha256": restored_sha,
    }
