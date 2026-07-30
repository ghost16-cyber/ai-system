from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from backend.app.project_control.contracts import content_hash
from backend.app.project_retrieval.contracts import CHUNKING_POLICY_VERSION, DocumentChunk


TARGET_CHARS = 4_000
MAX_CHARS = 6_000
OVERLAP_LINES = 8
MAX_CHUNKS_PER_SOURCE = 256
MAX_SOURCE_BYTES = 750_000
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")


def chunk_source(
    *,
    source_id: str,
    project_id: str,
    relative_path: str,
    source_content_hash: str,
    text: str,
    created_at: datetime | None = None,
) -> tuple[DocumentChunk, ...]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return ()
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    chunks: list[DocumentChunk] = []
    start = 0
    headings: list[str] = []
    now = created_at or datetime.now(timezone.utc)
    while start < len(lines) and len(chunks) < MAX_CHUNKS_PER_SOURCE:
        end = start
        size = 0
        local_headings = list(headings)
        while end < len(lines):
            line = lines[end]
            match = _HEADING.match(line)
            if match:
                depth = len(match.group(1))
                local_headings = local_headings[: depth - 1] + [match.group(2).strip()[:200]]
            if end > start and size + len(line) > TARGET_CHARS:
                break
            size += len(line)
            end += 1
            if size >= MAX_CHARS:
                break
        if end == start:
            end = start + 1
        raw = "".join(lines[start:end]).rstrip()
        if raw:
            normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
            material = {
                "source_content_hash": source_content_hash,
                "relative_path": relative_path,
                "chunking_policy_version": CHUNKING_POLICY_VERSION,
                "start_offset": offsets[start],
                "end_offset": offsets[end - 1] + len(lines[end - 1]),
                "normalized_text_hash": content_hash(normalized),
            }
            digest = content_hash(material)
            chunks.append(DocumentChunk(
                chunk_id=f"rag-chunk-{digest[:32]}",
                source_id=source_id,
                project_id=project_id,
                relative_path=relative_path,
                ordinal=len(chunks),
                start_offset=material["start_offset"],
                end_offset=material["end_offset"],
                start_line=start + 1,
                end_line=end,
                heading_path=tuple(local_headings),
                language=_language(relative_path),
                text=normalized[:MAX_CHARS],
                normalized_text_hash=material["normalized_text_hash"],
                source_content_hash=source_content_hash,
                token_estimate=max(1, (len(normalized) + 3) // 4),
                created_at=now,
            ))
        headings = local_headings
        if end >= len(lines):
            break
        start = max(start + 1, end - OVERLAP_LINES)
    return tuple(chunks)


def _language(path: str) -> str:
    return {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".md": "markdown",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".sql": "sql",
        ".sh": "shell", ".ps1": "powershell", ".html": "html", ".css": "css",
    }.get(PurePosixPath(path).suffix.casefold(), "text")

