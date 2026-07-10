from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from backend.app.core.path_utils import normalize_path_for_platform
from backend.app.assignments.schemas import ParsedAssignmentDocument


SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx"}
DOCX_MISSING_MESSAGE = "python-docx is required to parse .docx files. Install python-docx or paste the assignment text."


def parse_assignment_document(path: str | Path) -> ParsedAssignmentDocument:
    source = normalize_path_for_platform(path).path
    if not source.exists():
        raise FileNotFoundError(f"Assignment document not found: {source}")
    if not source.is_file():
        raise ValueError("Assignment document path must point to a file, but this path is a folder.")
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("Unsupported assignment document extension. Supported extensions: .docx, .md, .txt.")

    warnings: list[str] = []
    if suffix in {".txt", ".md"}:
        text = source.read_text(encoding="utf-8", errors="ignore")
    else:
        text, docx_warnings = _read_docx(source)
        warnings.extend(docx_warnings)
    text = _normalize_text(text)
    title = _title_from_text(text) or source.stem.replace("_", " ").replace("-", " ").strip()
    return ParsedAssignmentDocument(
        document_id=_document_id(source, text),
        title=title,
        source_path=str(source),
        extracted_text=text,
        created_at=datetime.now(UTC),
        warnings=warnings,
    )


def _read_docx(path: Path) -> tuple[str, list[str]]:
    try:
        from docx import Document
    except ImportError as error:
        raise ValueError(DOCX_MISSING_MESSAGE) from error
    document = Document(str(path))
    paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    return "\n".join(paragraphs), []


def _normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized = "\n".join(lines)
    while "\n\n\n" in normalized:
        normalized = normalized.replace("\n\n\n", "\n\n")
    return normalized.strip()


def _title_from_text(text: str) -> str | None:
    for line in text.splitlines():
        cleaned = line.strip().strip("#").strip()
        if cleaned:
            return cleaned[:120]
    return None


def _document_id(path: Path, text: str) -> str:
    digest = hashlib.sha256(f"{path.resolve()}:{text[:2000]}".encode("utf-8")).hexdigest()[:16]
    return f"assignment-{digest}"
