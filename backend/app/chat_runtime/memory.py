from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.project_control.contracts import (
    ProjectReadModel,
    StrictModel,
    canonical_json,
    content_hash,
)
from backend.app.schemas.api import ChatRunResponse


MAX_WORKING_MEMORY_BYTES = 8_192
MAX_RECENT_TURNS = 4
MAX_RETAINED_CONSTRAINTS = 6

_SENSITIVE_TERMS = (
    "api key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private key",
    "secret",
    "token",
)
_CONSTRAINT_PATTERN = re.compile(
    r"\b(?:avoid|do not|don't|leave\b.+\bto me|must|mustn't|never|only|"
    r"prefer|require|required|should not|without)\b",
    re.IGNORECASE,
)
_FOLLOWUP_PATTERN = re.compile(
    r"\b(?:again|also|continue|earlier|follow[- ]?up|last|previous|same|"
    r"that|them|this|those|what did i|what was|you said)\b",
    re.IGNORECASE,
)
_TOKEN_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_.:/-]{3,}")
_TOPIC_STOPWORDS = frozenset({
    "about", "after", "again", "also", "another", "before", "could", "from",
    "have", "into", "just", "more", "please", "should", "that", "their",
    "there", "these", "they", "this", "those", "what", "when", "where",
    "which", "with", "would", "your",
})


class ChatMemoryTurn(StrictModel):
    sequence: int = Field(ge=1)
    user_request: str = Field(min_length=1, max_length=240)
    assistant_outcome: str = Field(min_length=1, max_length=320)
    specialist: str = Field(min_length=1, max_length=160)
    intent: str = Field(min_length=1, max_length=160)


class ChatProjectMemory(StrictModel):
    project_run_id: str = Field(min_length=1, max_length=200)
    lifecycle_state: str = Field(min_length=1, max_length=100)
    state_version: int = Field(ge=1)
    plan_revision_id: str | None = Field(default=None, max_length=200)
    scope_revision_id: str | None = Field(default=None, max_length=200)
    manifest_hash: str | None = Field(default=None, min_length=64, max_length=64)
    current_work_unit: str | None = Field(default=None, max_length=200)
    pending_user_action: str | None = Field(default=None, max_length=300)
    progress: dict[str, int] = Field(default_factory=dict)
    verification_summary: dict[str, int] = Field(default_factory=dict)
    blocked_reason: str | None = Field(default=None, max_length=500)
    terminal: bool
    advisory_only: Literal[True] = True
    authority_granted: Literal[False] = False


class ChatWorkingMemory(StrictModel):
    schema_version: Literal["astra.chat-runtime.working-memory.v1"] = (
        "astra.chat-runtime.working-memory.v1"
    )
    memory_identity: str = Field(default="", max_length=64)
    conversation_id: str = Field(min_length=1, max_length=200)
    total_prior_turns: int = Field(ge=0)
    first_user_request: str | None = Field(default=None, max_length=240)
    retained_user_constraints: tuple[str, ...] = ()
    recent_turns: tuple[ChatMemoryTurn, ...] = ()
    active_project: ChatProjectMemory | None = None
    advisory_only: Literal[True] = True
    authority_granted: Literal[False] = False

    @model_validator(mode="after")
    def bind_and_bound(self) -> "ChatWorkingMemory":
        expected = content_hash(
            self.model_dump(mode="json", exclude={"memory_identity"})
        )
        if self.memory_identity and self.memory_identity != expected:
            raise ValueError("working memory identity does not match its content")
        object.__setattr__(self, "memory_identity", expected)
        encoded = canonical_json(self.model_dump(mode="json")).encode("utf-8")
        if len(encoded) > MAX_WORKING_MEMORY_BYTES:
            raise ValueError("working memory exceeds its byte limit")
        if len(self.recent_turns) > MAX_RECENT_TURNS:
            raise ValueError("working memory contains too many recent turns")
        if len(self.retained_user_constraints) > MAX_RETAINED_CONSTRAINTS:
            raise ValueError("working memory contains too many retained constraints")
        return self


def build_chat_working_memory(
    *,
    conversation_id: str,
    latest_message: str,
    previous_turns: list[ChatRunResponse],
    active_project: ProjectReadModel | None,
) -> ChatWorkingMemory | None:
    constraints = _retained_constraints(previous_turns)
    if not _should_attach(
        latest_message,
        previous_turns,
        has_constraints=bool(constraints),
        has_project=active_project is not None,
    ):
        return None
    selected = previous_turns[-MAX_RECENT_TURNS:]
    start_sequence = len(previous_turns) - len(selected) + 1
    recent_turns = tuple(
        ChatMemoryTurn(
            sequence=start_sequence + offset,
            user_request=_safe_text(turn.user_message, 240),
            assistant_outcome=_safe_text(turn.assistant_response, 320),
            specialist=_safe_text(turn.selected_specialist, 160),
            intent=_safe_text(turn.intent, 160),
        )
        for offset, turn in enumerate(selected)
    )
    project_memory = (
        ChatProjectMemory(
            project_run_id=active_project.project_run_id,
            lifecycle_state=active_project.lifecycle_state,
            state_version=active_project.state_version,
            plan_revision_id=active_project.plan_revision_id,
            scope_revision_id=active_project.scope_revision_id,
            manifest_hash=active_project.manifest_hash,
            current_work_unit=active_project.current_work_unit,
            pending_user_action=active_project.pending_user_action,
            progress=dict(active_project.progress),
            verification_summary=dict(active_project.verification_summary),
            blocked_reason=(
                _safe_text(active_project.blocked_reason, 500)
                if active_project.blocked_reason
                else None
            ),
            terminal=active_project.terminal,
        )
        if active_project is not None
        else None
    )
    return ChatWorkingMemory(
        conversation_id=conversation_id,
        total_prior_turns=len(previous_turns),
        first_user_request=(
            _safe_text(previous_turns[0].user_message, 240)
            if previous_turns
            else None
        ),
        retained_user_constraints=constraints,
        recent_turns=recent_turns,
        active_project=project_memory,
    )


def render_chat_working_memory(memory: ChatWorkingMemory) -> str:
    return canonical_json(memory.model_dump(mode="json"))


def _should_attach(
    latest_message: str,
    previous_turns: list[ChatRunResponse],
    *,
    has_constraints: bool,
    has_project: bool,
) -> bool:
    lowered = " ".join(latest_message.lower().split())
    if not lowered or _is_greeting(lowered):
        return False
    if has_project or has_constraints:
        return True
    if not previous_turns:
        return False
    if lowered.startswith(("and ", "but ", "so ")):
        return True
    if _FOLLOWUP_PATTERN.search(lowered):
        return True
    latest_tokens = _topic_tokens(latest_message)
    prior_tokens: set[str] = set()
    for turn in previous_turns[-2:]:
        prior_tokens.update(_topic_tokens(turn.user_message))
    return len(latest_tokens & prior_tokens) >= 2


def _retained_constraints(
    previous_turns: list[ChatRunResponse],
) -> tuple[str, ...]:
    retained: list[str] = []
    seen: set[str] = set()
    for turn in reversed(previous_turns):
        sentences = re.split(r"(?<=[.!?])\s+|\r?\n+", turn.user_message)
        for sentence in reversed(sentences):
            normalized = " ".join(sentence.split())
            identity = normalized.casefold()
            if (
                normalized
                and identity not in seen
                and _CONSTRAINT_PATTERN.search(normalized)
            ):
                seen.add(identity)
                retained.append(_safe_text(normalized, 240))
                if len(retained) >= MAX_RETAINED_CONSTRAINTS:
                    return tuple(reversed(retained))
    return tuple(reversed(retained))


def _topic_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_PATTERN.findall(text)
        if token.casefold() not in _TOPIC_STOPWORDS
    }


def _safe_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    lowered = text.casefold()
    if any(term in lowered for term in _SENSITIVE_TERMS):
        text = "[sensitive-looking content omitted from working memory]"
    if not text:
        text = "[empty]"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _is_greeting(message: str) -> bool:
    return message.strip(" .!?").casefold() in {
        "hello",
        "hello there",
        "hey",
        "hi",
        "hi there",
    }


__all__ = [
    "MAX_RECENT_TURNS",
    "MAX_RETAINED_CONSTRAINTS",
    "MAX_WORKING_MEMORY_BYTES",
    "ChatMemoryTurn",
    "ChatProjectMemory",
    "ChatWorkingMemory",
    "build_chat_working_memory",
    "render_chat_working_memory",
]
