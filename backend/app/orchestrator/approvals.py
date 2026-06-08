from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .checkpoints import create_file_checkpoint, rollback_checkpoint, sha256_text
from .git_safety import get_dirty_worktree_status
from .policy import PolicyError, SafetyPolicy, validate_patch_scope


def save_pending_approval(
    *,
    approval_root: str | Path,
    task_id: str,
    workspace_root: Path,
    project_path: str,
    patch: dict[str, Any],
    confidence: dict[str, Any] | None,
    allow_tests: bool,
    allow_dirty_worktree: bool,
    rollback_on_test_failure: bool,
    checkpoint_root: str | Path,
) -> dict[str, Any]:
    root = Path(approval_root)
    root.mkdir(parents=True, exist_ok=True)
    approval_path = root / f"{task_id}.json"
    record = {
        "approval_id": task_id,
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace_root": workspace_root.resolve().as_posix(),
        "project_path": project_path,
        "patch": patch,
        "confidence": confidence,
        "allow_tests": allow_tests,
        "allow_dirty_worktree": allow_dirty_worktree,
        "rollback_on_test_failure": rollback_on_test_failure,
        "checkpoint_root": str(checkpoint_root),
        "status": "pending",
    }
    approval_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "approval_id": task_id,
        "approval_path": approval_path.as_posix(),
        "status": "pending",
    }


def approve_pending_patch(
    *,
    approval_root: str | Path,
    approval_id: str,
) -> dict[str, Any]:
    approval_path = Path(approval_root) / f"{approval_id}.json"
    if not approval_path.exists():
        raise LookupError("Pending approval was not found.")

    record = json.loads(approval_path.read_text(encoding="utf-8"))
    if record.get("status") != "pending":
        raise ValueError("Pending approval has already been processed.")

    workspace_root = Path(str(record["workspace_root"])).resolve()
    project_path = str(record["project_path"])
    patch = record["patch"]
    policy = SafetyPolicy(workspace_root, project_path)
    resolved = policy.resolve_patch_path(str(patch.get("path", "")))
    scope = validate_patch_scope(patch)
    if not scope["valid"]:
        raise PolicyError(str(scope["reason"]))

    dirty = get_dirty_worktree_status(policy.project_root, resolved.absolute)
    if dirty.dirty and not bool(record.get("allow_dirty_worktree")):
        raise PolicyError("Dirty working tree detected; approval cannot apply patch.")

    source = resolved.absolute.read_text(encoding="utf-8")
    old = str(patch.get("old", ""))
    new = str(patch.get("new", ""))
    if source.count(old) != 1:
        raise PolicyError("Patch old text must still occur exactly once.")
    updated = source.replace(old, new, 1)
    if resolved.absolute.suffix.lower() == ".py":
        ast.parse(updated)

    checkpoint = create_file_checkpoint(
        checkpoint_root=str(record["checkpoint_root"]),
        task_id=str(record["task_id"]),
        project_root=policy.project_root,
        relative_path=resolved.relative,
        patch=patch,
    )
    resolved.absolute.write_text(updated, encoding="utf-8")
    result: dict[str, Any] = {
        "approval_id": approval_id,
        "applied": True,
        "path": resolved.relative,
        "checkpoint": checkpoint,
        "dirty_worktree": dirty.to_dict(),
        "updated_sha256": sha256_text(updated),
    }

    if bool(record.get("allow_tests")):
        tests = _run_pytest(policy.project_root)
        result["tests"] = tests
        if (
            tests["status"] == "failed"
            and bool(record.get("rollback_on_test_failure", True))
        ):
            rollback = rollback_checkpoint(str(checkpoint["checkpoint_path"]))
            result["rollback"] = rollback
            result["applied"] = False

    record["status"] = "applied" if result.get("applied") else "rolled_back"
    record["processed_at"] = datetime.now(timezone.utc).isoformat()
    record["result"] = result
    approval_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def _run_pytest(project_root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=project_root,
        capture_output=True,
        env=_test_env(),
        text=True,
        timeout=60,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    if len(output) > 4000:
        output = output[-4000:]
    return {
        "command": "python -m pytest -q",
        "exit_code": completed.returncode,
        "status": "passed" if completed.returncode == 0 else "failed",
        "output": output,
    }


def _test_env() -> dict[str, str]:
    import os

    env = os.environ.copy()
    env["TMP"] = "/tmp"
    env["TEMP"] = "/tmp"
    env["TMPDIR"] = "/tmp"
    return env
