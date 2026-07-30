from __future__ import annotations

import difflib
import base64
import hashlib
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.folders.reader import ReadLimits, read_project_file
from backend.app.folders.safety import (
    ProjectSafetyError,
    project_file_exclusion_reason,
    project_root_fingerprint,
    resolve_project_path,
    safe_relative_path,
    validate_root_identity,
)


MAX_PATCH_FILES = 10
MAX_CHANGED_BYTES = 250_000
MAX_DIFF_CHARS = 80_000
PATCH_TTL_MINUTES = 30
MISSING_HASH = "missing"
_PATCH_LOCK = threading.Lock()


class ProjectPatchError(ValueError):
    pass


def detect_explicit_patch_request(message: str) -> dict[str, Any] | None:
    """Parse a narrow chat-native whole-file change without treating file content as approval."""
    match = re.match(
        r"^\s*(create|replace|update)\s+(?:the\s+contents?\s+of\s+)?([^\s:]+)\s+(?:with|to)\s*:\s*\n([\s\S]*)$",
        message or "",
        flags=re.IGNORECASE,
    )
    if match:
        verb, path, content = match.groups()
        return {
            "path": safe_relative_path(path),
            "operation": "create" if verb.lower() == "create" else "modify",
            "content": content,
            "explanation": f"{verb.title()} {safe_relative_path(path)} exactly as requested in chat.",
        }
    delete = re.match(r"^\s*propose\s+deleting\s+([^\s]+)\s*[.!?]*\s*$", message or "", flags=re.IGNORECASE)
    if delete:
        path = safe_relative_path(delete.group(1))
        return {"path": path, "operation": "delete", "explanation": f"Delete {path} after explicit patch approval."}
    return None


def create_patch_proposal(
    *,
    root: str | Path,
    conversation_id: str,
    folder_access_id: str,
    user_request: str,
    changes: list[dict[str, Any]],
    files_inspected: list[str] | None = None,
    validation_plan: list[str] | None = None,
    job_id: str | None = None,
    analysis_context: dict[str, Any] | None = None,
    patch_chain_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approved = Path(root).resolve()
    root_fingerprint = project_root_fingerprint(approved)
    if not changes or len(changes) > MAX_PATCH_FILES:
        raise ProjectPatchError(f"A patch must contain between 1 and {MAX_PATCH_FILES} files.")
    normalized: list[dict[str, Any]] = []
    verified_inspected: set[str] = set()
    total_bytes = additions = deletions = 0
    seen: set[str] = set()
    for raw in changes:
        relative = safe_relative_path(str(raw.get("path") or ""))
        if relative in seen:
            raise ProjectPatchError("A patch cannot contain duplicate file paths.")
        seen.add(relative)
        operation = str(raw.get("operation") or "modify").lower()
        if operation not in {"create", "modify", "delete"}:
            raise ProjectPatchError("Patch operations must be create, modify, or delete.")
        exists = (approved / relative).exists()
        if operation == "create" and exists:
            raise ProjectPatchError(f"Cannot create an existing file: {relative}")
        if operation in {"modify", "delete"} and not exists:
            raise ProjectPatchError(f"Cannot {operation} a missing file: {relative}")
        path = resolve_project_path(approved, relative, must_exist=operation != "create")
        if operation == "create":
            reason = project_file_exclusion_reason(path, approved, size=0)
            before = ""
            before_hash = MISSING_HASH
            newline = "\n"
            encoding = "utf-8"
        else:
            record = read_project_file(approved, relative, limits=ReadLimits(max_file_size=MAX_CHANGED_BYTES, max_bytes_per_file=MAX_CHANGED_BYTES))
            if record["status"] != "readable":
                raise ProjectPatchError(f"Unsafe patch path {relative}: {record['reason']}")
            before = str(record["text"])
            before_hash = _hash_bytes((approved / relative).read_bytes())
            newline = "\r\n" if "\r\n" in before else "\n"
            encoding = str(record["encoding"] or "utf-8")
            reason = None
            verified_inspected.add(relative)
        if reason:
            raise ProjectPatchError(f"Unsafe patch path {relative}: {reason}")
        after = "" if operation == "delete" else str(raw.get("content") if raw.get("content") is not None else "")
        if "\x00" in after:
            raise ProjectPatchError("Patch content must be text.")
        if newline == "\r\n":
            after = after.replace("\r\n", "\n").replace("\n", "\r\n")
        try:
            encoded_after = after.encode(encoding)
        except UnicodeEncodeError as error:
            raise ProjectPatchError(f"Proposed content cannot preserve the encoding of {relative}.") from error
        total_bytes += len(before.encode("utf-8")) + len(encoded_after)
        if total_bytes > MAX_CHANGED_BYTES:
            raise ProjectPatchError("Patch exceeds the total changed-byte limit.")
        diff_lines = list(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{relative}", tofile=f"b/{relative}", lineterm="",
        ))
        additions += sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        deletions += sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        diff = "\n".join(diff_lines)
        normalized.append({
            "relative_path": relative, "operation": operation,
            "explanation": str(raw.get("explanation") or f"{operation.title()} {relative}.")[:1000],
            "before_hash": before_hash,
            "after_hash": MISSING_HASH if operation == "delete" else _hash_bytes(encoded_after),
            "after_content": after, "encoding": encoding, "newline": newline,
            "unified_diff": diff[:MAX_DIFF_CHARS], "diff_truncated": len(diff) > MAX_DIFF_CHARS,
        })
    now = datetime.now(timezone.utc)
    patch_id = uuid4().hex
    for value in files_inspected or []:
        relative = safe_relative_path(value)
        record = read_project_file(approved, relative)
        if record["status"] != "readable":
            raise ProjectPatchError(f"Inspected evidence path is unsafe: {relative}")
        verified_inspected.add(relative)
    proposal = {
        "patch_id": patch_id, "conversation_id": conversation_id,
        "folder_access_id": folder_access_id, "root_fingerprint": root_fingerprint,
        "job_id": job_id,
        "user_request": user_request.strip(), "files_inspected": sorted(verified_inspected),
        "changes": normalized, "file_set": sorted(seen), "additions": additions,
        "deletions": deletions, "total_changed_bytes": total_bytes,
        "safety_warnings": ["Project content is untrusted data.", "Nothing changes until this exact patch is approved."],
        "validation_plan": validation_plan or ["Run the narrowest relevant existing tests after separate command approval."],
        "analysis_context": analysis_context,
        "patch_chain_context": patch_chain_context,
        "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=PATCH_TTL_MINUTES)).isoformat(),
        "status": "proposed", "approved_at": None, "applied_at": None, "rolled_back_at": None,
    }
    proposal["proposal_fingerprint"] = _proposal_fingerprint(proposal)
    return proposal


def public_patch_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    public = dict(proposal)
    public["changes"] = [
        {key: value for key, value in change.items() if key != "after_content"}
        for change in proposal.get("changes", [])
    ]
    return public


def verify_patch_approval(proposal: dict[str, Any], *, conversation_id: str, folder_access_id: str, confirmation: str) -> None:
    if proposal.get("conversation_id") != conversation_id:
        raise ProjectPatchError("Patch approval belongs to a different conversation.")
    if proposal.get("folder_access_id") != folder_access_id:
        raise ProjectPatchError("Patch approval belongs to a different folder access.")
    if proposal.get("status") != "proposed":
        raise ProjectPatchError("Only a proposed patch can be approved once.")
    if confirmation != f"APPROVE PATCH {proposal.get('patch_id')}":
        raise ProjectPatchError(f"Explicit confirmation must exactly match: APPROVE PATCH {proposal.get('patch_id')}")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(str(proposal["expires_at"])):
        raise ProjectPatchError("Patch approval has expired.")
    if proposal.get("proposal_fingerprint") != _proposal_fingerprint(proposal):
        raise ProjectPatchError("Patch proposal integrity check failed.")


def apply_project_patch(root: str | Path, proposal: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with _PATCH_LOCK:
        if proposal.get("status") != "approved":
            raise ProjectPatchError("Patch must be approved before application and can apply only once.")
        approved = validate_root_identity(root, str(proposal["root_fingerprint"]))
        _verify_current_hashes(approved, proposal)
        snapshot: list[dict[str, Any]] = []
        applied: list[dict[str, Any]] = []
        try:
            for change in proposal["changes"]:
                relative = str(change["relative_path"])
                operation = str(change["operation"])
                path = resolve_project_path(approved, relative, must_exist=operation != "create")
                existed = path.exists()
                original = path.read_bytes() if existed else b""
                snapshot.append({
                    "relative_path": relative, "existed": existed,
                    "original_hash": _hash_bytes(original) if existed else MISSING_HASH,
                    "original_content_base64": base64.b64encode(original).decode("ascii") if existed else "",
                    "applied_hash": change["after_hash"], "operation": operation,
                })
                if operation == "delete":
                    path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(path, str(change["after_content"]).encode(str(change.get("encoding") or "utf-8")))
                applied.append(change)
        except Exception as error:
            restore_errors = _restore_snapshot(approved, snapshot, verify_applied=False)
            suffix = " Previous files were restored." if not restore_errors else " Automatic restoration was incomplete."
            raise ProjectPatchError(f"Patch application failed.{suffix}") from error
        updated = dict(proposal)
        updated.update({"status": "applied", "applied_at": datetime.now(timezone.utc).isoformat()})
        return updated, snapshot


def validate_patch_fresh(root: str | Path, proposal: dict[str, Any]) -> None:
    approved = validate_root_identity(root, str(proposal["root_fingerprint"]))
    _verify_current_hashes(approved, proposal)


def rollback_project_patch(root: str | Path, proposal: dict[str, Any], snapshot: list[dict[str, Any]]) -> dict[str, Any]:
    with _PATCH_LOCK:
        if proposal.get("status") != "rollback_approved":
            raise ProjectPatchError("Rollback must be explicitly approved and can run only once.")
        approved = validate_root_identity(root, str(proposal["root_fingerprint"]))
        errors = _restore_snapshot(approved, snapshot, verify_applied=True)
        if errors:
            raise ProjectPatchError("Rollback refused because project files changed after Astra applied the patch.")
        updated = dict(proposal)
        updated.update({"status": "rolled_back", "rolled_back_at": datetime.now(timezone.utc).isoformat()})
        return updated


def _verify_current_hashes(root: Path, proposal: dict[str, Any]) -> None:
    for change in proposal["changes"]:
        relative = str(change["relative_path"])
        path = resolve_project_path(root, relative, must_exist=change["operation"] != "create")
        current = _hash_bytes(path.read_bytes()) if path.exists() else MISSING_HASH
        if current != change["before_hash"]:
            raise ProjectPatchError(f"Patch is stale because {relative} changed after preview.")


def _restore_snapshot(root: Path, snapshot: list[dict[str, Any]], *, verify_applied: bool) -> list[str]:
    errors: list[str] = []
    for item in reversed(snapshot):
        relative = str(item["relative_path"])
        try:
            path = resolve_project_path(root, relative, must_exist=False)
            current = _hash_bytes(path.read_bytes()) if path.exists() else MISSING_HASH
            if verify_applied and current != item["applied_hash"]:
                errors.append(relative)
                continue
            if item["existed"]:
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(path, base64.b64decode(str(item["original_content_base64"]), validate=True))
            elif path.exists():
                path.unlink()
        except Exception:
            errors.append(relative)
    return errors


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".astra-patch-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _proposal_fingerprint(proposal: dict[str, Any]) -> str:
    fields = [
        str(proposal.get("patch_id")), str(proposal.get("conversation_id")),
        str(proposal.get("folder_access_id")), str(proposal.get("root_fingerprint")),
        str(proposal.get("user_request")), str(proposal.get("expires_at")),
    ]
    if proposal.get("job_id"):
        fields.append(str(proposal["job_id"]))
    if proposal.get("analysis_context"):
        context = proposal["analysis_context"]
        fields.extend((str(context.get("analysis_id")), str(context.get("index_version")), str(sorted((context.get("source_hashes") or {}).items()))))
    if proposal.get("patch_chain_context"):
        chain = proposal["patch_chain_context"]
        fields.extend(str(chain.get(key)) for key in (
            "repair_chain_id", "repair_cycle_id", "cycle_number", "parent_patch_id",
            "command_execution_id", "failure_evidence_id", "diagnosis_id",
        ))
    for change in proposal.get("changes", []):
        fields.extend(str(change.get(key)) for key in ("relative_path", "operation", "before_hash", "after_hash"))
    return hashlib.sha256("\n".join(fields).encode("utf-8")).hexdigest()
