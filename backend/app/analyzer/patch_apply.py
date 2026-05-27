from __future__ import annotations

import hashlib
from pathlib import Path

from backend.app.schemas.api import PatchApplyResponse, PatchProposalResponse


class PatchApplyConflictError(Exception):
    """Raised when the file changed after the patch proposal was created."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolve_workspace_file(workspace_root: Path, relative_path: str) -> Path:
    requested_path = Path(relative_path)

    if requested_path.is_absolute():
        raise ValueError("Patch paths must be relative to the configured workspace root.")

    resolved_path = (workspace_root / requested_path).resolve()

    try:
        resolved_path.relative_to(workspace_root)
    except ValueError as error:
        raise ValueError(
            "Patch path must stay within the configured workspace root."
        ) from error

    if resolved_path.suffix.lower() != ".py":
        raise ValueError("Only Python files can currently be patched.")

    if not resolved_path.exists():
        raise FileNotFoundError("Patch target file was not found.")

    if not resolved_path.is_file():
        raise ValueError("Patch target path is not a file.")

    return resolved_path


def apply_patch_proposal(
    *,
    workspace_root: Path,
    proposal: PatchProposalResponse,
) -> PatchApplyResponse:
    if proposal.status != "proposed":
        raise ValueError(f"Patch proposal is not applicable in status: {proposal.status}")

    if proposal.start_line < 1:
        raise ValueError("Patch start_line must be greater than or equal to 1.")

    if proposal.end_line < proposal.start_line:
        raise ValueError("Patch end_line must be greater than or equal to start_line.")

    target_path = _resolve_workspace_file(workspace_root, proposal.path)

    try:
        original_code = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Patch target file must be UTF-8 encoded.") from error

    current_sha256 = _sha256_text(original_code)

    if current_sha256 != proposal.original_file_sha256:
        raise PatchApplyConflictError(
            "Patch target file has changed since the proposal was created."
        )

    original_lines = original_code.splitlines(keepends=True)

    if proposal.end_line > len(original_lines):
        raise PatchApplyConflictError(
            "Patch target line range is no longer valid for this file."
        )

    replacement_lines = proposal.replacement.splitlines(keepends=True)

    updated_lines = (
        original_lines[: proposal.start_line - 1]
        + replacement_lines
        + original_lines[proposal.end_line :]
    )
    updated_code = "".join(updated_lines)
    updated_sha256 = _sha256_text(updated_code)

    target_path.write_text(updated_code, encoding="utf-8")

    return PatchApplyResponse(
        proposal_id=proposal.proposal_id,
        analysis_id=proposal.analysis_id,
        finding_id=proposal.finding_id,
        path=proposal.path,
        status="applied",
        applied=True,
        original_file_sha256=current_sha256,
        updated_file_sha256=updated_sha256,
        message="Patch applied successfully.",
    )