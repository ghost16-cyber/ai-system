from __future__ import annotations

from difflib import unified_diff
from pathlib import Path

from backend.app.schemas.api import PatchPreviewResponse, PatchProposalResponse

from .patch_apply import resolve_workspace_file, sha256_text


def preview_patch_proposal(
    *,
    workspace_root: Path,
    proposal: PatchProposalResponse,
) -> PatchPreviewResponse:
    if proposal.status != "proposed":
        raise ValueError(
            f"Patch proposal is not previewable in status: {proposal.status}"
        )
    if proposal.start_line < 1:
        raise ValueError("Patch start_line must be greater than or equal to 1.")
    if proposal.end_line < proposal.start_line:
        raise ValueError("Patch end_line must be greater than or equal to start_line.")

    target_path = resolve_workspace_file(workspace_root, proposal.path)

    try:
        current_code = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Patch target file must be UTF-8 encoded.") from error

    current_sha256 = sha256_text(current_code)
    if current_sha256 != proposal.original_file_sha256:
        return PatchPreviewResponse(
            proposal_id=proposal.proposal_id,
            path=proposal.path,
            status=proposal.status,
            original_file_sha256=proposal.original_file_sha256,
            current_file_sha256=current_sha256,
            preview_available=False,
        )

    original_lines = current_code.splitlines(keepends=True)
    if proposal.end_line > len(original_lines):
        raise ValueError("Patch target line range is no longer valid for this file.")

    replacement_lines = proposal.replacement.splitlines(keepends=True)
    updated_lines = (
        original_lines[: proposal.start_line - 1]
        + replacement_lines
        + original_lines[proposal.end_line :]
    )
    diff = "".join(
        unified_diff(
            original_lines,
            updated_lines,
            fromfile=f"a/{proposal.path}",
            tofile=f"b/{proposal.path}",
        )
    )

    return PatchPreviewResponse(
        proposal_id=proposal.proposal_id,
        path=proposal.path,
        status=proposal.status,
        unified_diff=diff,
        original_file_sha256=proposal.original_file_sha256,
        current_file_sha256=current_sha256,
        preview_available=True,
    )
