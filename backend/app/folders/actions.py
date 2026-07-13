from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.core.path_utils import normalize_path_for_platform
from backend.app.folders.scanner import safe_display_path
from backend.app.schemas.api import ChatRunResponse


_FOLDER_REQUEST_PATTERNS = (
    re.compile(r"^\s*use\s+(?P<path>.+?)\s*$", re.IGNORECASE),
    re.compile(
        r"^\s*(?:please\s+)?connect\s+(?:the\s+)?(?:project\s+)?folder\s*:?[ \t]+(?P<path>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:please\s+)?open\s+(?:the\s+)?project\s+(?:at|in)\s+(?P<path>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:please\s+)?give\s+astra\s+(?:read-only\s+)?access\s+to\s+(?P<path>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:please\s+)?(?:read|open|inspect|summari[sz]e|analy[sz]e)\s+"
        r"(?:all\s+)?(?:the\s+)?files\s+(?:at|in|under|from)\s+(?P<path>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*open\s+this\s+project\s+folder\s*:?\s+(?P<path>.+?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*connect\s+this\s+folder\s+in\s+read-only\s+mode\s*:?\s+(?P<path>.+?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*connect\s+this\s+folder\s+in\s+readonly\s+mode\s*:?\s+(?P<path>.+?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*scan\s+my\s+assignment\s+folder\s+(?:at|in)\s+(?P<path>.+?)\s*$", re.IGNORECASE),
)

_FOLDER_CONTENT_REQUEST_PATTERNS = (
    re.compile(
        r"^\s*(?:please\s+)?list\s+and\s+summari[sz]e\s+(?:the\s+)?files\s+"
        r"in\s+(?:the\s+)?(?:connected\s+)?project\s*[.!?]*\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:please\s+)?(?:read|open|inspect|summari[sz]e|analy[sz]e)\s+"
        r"(?:(?:all|the|those|these)\s+)?(?:folder\s+)?files\s*[.!?]*\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:please\s+)?(?:read|open|inspect|summari[sz]e|analy[sz]e)\s+"
        r"(?:the\s+)?file\s+contents?\s*[.!?]*\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:please\s+)?summari[sz]e\s+(?:the\s+)?folder(?:\s+files)?\s*[.!?]*\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:please\s+)?(?:read|open|inspect|summari[sz]e|analy[sz]e)\s+"
        r"(?:the\s+)?(?:assignment|dataset)\s*[.!?]*\s*$",
        re.IGNORECASE,
    ),
)

FOLDER_CONTENT_UNAVAILABLE_RESPONSE = (
    "That folder is connected in metadata-only mode. I can see its inventory, "
    "but file-content reading is not enabled yet."
)


def detect_folder_request(message: str) -> str | None:
    """Return a requested folder path only for explicit chat-native folder intents."""
    text = (message or "").strip()
    if not text:
        return None

    for pattern in _FOLDER_REQUEST_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        path = _strip_wrapping_quotes(match.group("path"))
        if _looks_like_folder_path(path):
            return path
    return None


def is_folder_content_request(message: str) -> bool:
    """Recognize deterministic requests for content from an already connected folder."""
    return any(pattern.match(message or "") for pattern in _FOLDER_CONTENT_REQUEST_PATTERNS)


def has_completed_folder_action(previous_turns: list[ChatRunResponse]) -> bool:
    """Return whether this conversation already contains a connected folder."""
    return any(
        isinstance(turn.action, dict)
        and turn.action.get("action_type") == "folder_access"
        and turn.action.get("status") == "completed"
        for turn in previous_turns
    )


def completed_folder_access(previous_turns: list[ChatRunResponse]) -> dict | None:
    """Return the latest valid completed folder access metadata in this conversation."""
    for turn in reversed(previous_turns):
        action = turn.action
        if not isinstance(action, dict) or action.get("action_type") != "folder_access" or action.get("status") != "completed":
            continue
        technical = action.get("technical_details")
        folder = technical.get("folder_action") if isinstance(technical, dict) else None
        if isinstance(folder, dict) and folder.get("status") == "completed" and folder.get("approved_root"):
            return {**folder, "action_id": action.get("action_id")}
    return None


def build_folder_action(requested_path: str, *, action_id: str | None = None) -> dict:
    action_id = action_id or str(uuid4())
    display_path = _safe_requested_display(requested_path)
    folder_action = {
        "action_id": action_id,
        "status": "awaiting_approval",
        "requested_path": requested_path,
        "display_path": display_path,
        "approved_root": None,
        "approved_root_display": None,
        "inventory": [],
        "summary": {
            "total_discovered": 0,
            "readable": 0,
            "ignored": 0,
            "assignments": 0,
            "datasets": 0,
            "source_files": 0,
            "reports": 0,
            "evidence_files": 0,
            "configuration_files": 0,
            "other_files": 0,
            "warning_count": 0,
        },
        "warnings": [],
        "diff": {"added": 0, "changed": 0, "deleted": 0, "unchanged": 0},
        "scan_count": 0,
        "last_scanned_at": None,
        "error": None,
    }
    return {
        "action_id": action_id,
        "action_type": "folder_access",
        "title": "Folder access requested",
        "summary": "Approve read-only access before Astra scans this folder.",
        "steps": [
            "Approve read-only folder access",
            "Scan safe metadata only",
            "Review the persistent inventory",
        ],
        "safety_information": {
            "approval_required": True,
            "read_only": True,
            "writes_blocked": True,
            "execution_blocked": True,
            "file_contents_not_persisted": True,
            "expected_file_modifications": "None. This action only scans metadata after approval.",
        },
        "status": "awaiting_approval",
        "approval_required": True,
        "result_summary": None,
        "error": None,
        "technical_details": {"folder_action": folder_action},
    }


def create_folder_chat_run(
    *,
    message: str,
    requested_path: str,
    conversation_id: str | None = None,
) -> ChatRunResponse:
    created_at = datetime.now(timezone.utc)
    return ChatRunResponse(
        run_id=str(uuid4()),
        conversation_id=conversation_id or str(uuid4()),
        user_message=message,
        assistant_response="Approve read-only folder access before I scan that project folder.",
        selected_specialist="folder_access",
        intent="folder_access",
        confidence=1.0,
        rag_used=False,
        rag_skip_reason="Direct folder access action intercepted before retrieval.",
        rag_context_count=0,
        runtime_decision="approval_required",
        safety_decision="approval_required",
        used_real_slm=False,
        slm_provider="not_invoked",
        slm_fallback_reason="Direct folder access did not require model generation.",
        memory_used=False,
        memory_summary=None,
        created_at=created_at,
        trace_summary=[
            {
                "phase": "folder_access_interception",
                "title": "Folder access requested",
                "detail": "Created a persisted read-only folder action before scanning.",
                "status": "passed",
            }
        ],
        action=build_folder_action(requested_path),
    )


def create_folder_content_unavailable_chat_run(
    *,
    message: str,
    conversation_id: str,
) -> ChatRunResponse:
    """Create a chat-native Stage 4A capability boundary response without an action."""
    created_at = datetime.now(timezone.utc)
    return ChatRunResponse(
        run_id=str(uuid4()),
        conversation_id=conversation_id,
        user_message=message,
        assistant_response=FOLDER_CONTENT_UNAVAILABLE_RESPONSE,
        selected_specialist="folder_access",
        intent="folder_content_unavailable",
        confidence=1.0,
        rag_used=False,
        rag_skip_reason="Connected folders support metadata inventory only.",
        rag_context_count=0,
        runtime_decision="not_applicable",
        safety_decision="unsupported_capability",
        used_real_slm=False,
        slm_provider="not_invoked",
        slm_fallback_reason="Deterministic metadata-only capability boundary response.",
        memory_used=True,
        memory_summary=None,
        created_at=created_at,
        trace_summary=[
            {
                "phase": "folder_content_interception",
                "title": "Folder content reading unavailable",
                "detail": "The existing folder inventory was preserved and no file content was read.",
                "status": "passed",
            }
        ],
        action=None,
    )


def _strip_wrapping_quotes(value: str) -> str:
    path = value.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in {"'", '"', "`"}:
        return path[1:-1].strip()
    return path


def _looks_like_folder_path(value: str) -> bool:
    path = value.strip()
    if not path or path in {".", ".."}:
        return False
    return (
        path.startswith("/")
        or path.startswith("~/")
        or path.startswith("./")
        or path.startswith("../")
        or "\\" in path
        or bool(re.match(r"^[A-Za-z]:[\\/]", path))
        or "/" in path
    )


def _safe_requested_display(value: str) -> str:
    try:
        return safe_display_path(normalize_path_for_platform(value.strip()).path)
    except Exception:
        return safe_display_path(Path(value.strip()))
