from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from backend.app.orchestrator.models import TaskState, ToolAction
from backend.app.slm.action_parser import (
    extract_json_object,
    normalize_action_payload,
)
from backend.app.slm.client import OllamaClient
from backend.app.slm.model_registry import build_ollama_client
from backend.app.slm.prompt_builder import build_action_prompt


class SLMProposedAction(BaseModel):
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str
    model: str
    advisory_only: bool = True


@dataclass
class SLMRouter:
    client: OllamaClient
    available_tools: list[dict[str, Any]]

    @classmethod
    def from_ollama(
        cls,
        *,
        available_tools: list[dict[str, Any]],
        model: str = "qwen2.5-coder:1.5b",
        base_url: str | None = None,
        timeout_seconds: int = 90,
    ) -> "SLMRouter":
        return cls(
            client=build_ollama_client(
                model=model,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
            ),
            available_tools=available_tools,
        )

    def propose_action(self, state: TaskState) -> SLMProposedAction:
        prompt = build_action_prompt(
            state=state,
            available_tools=self.available_tools,
        )
        raw = self.client.generate(prompt)
        payload = normalize_action_payload(extract_json_object(raw))
        return SLMProposedAction(
            action=str(payload["action"]),
            args=payload["args"],
            reason=payload["reason"],
            model=self.client.model,
        )

    def propose_tool_action(self, state: TaskState) -> ToolAction:
        proposed = self.propose_action(state)
        return ToolAction(
            action=proposed.action,  # type: ignore[arg-type]
            args=proposed.args,
            reason=proposed.reason,
        )
