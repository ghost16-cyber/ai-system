from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Any, Protocol

from backend.app.local_ai.contracts import CapabilityStatus, ProviderProfile


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    request_id: str
    model_profile_id: str
    prompt: str
    context_limit: int
    output_limit: int
    thinking_mode: bool
    timeout_seconds: int
    expected_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    request_id: str
    final_text: str
    structured_output: dict[str, Any] | None
    provider_metadata: dict[str, Any]


class LocalModelProvider(Protocol):
    profile: ProviderProfile

    def invoke(self, request: ProviderRequest, cancellation: Event) -> ProviderResponse: ...


class UnavailableProvider:
    def __init__(self, profile: ProviderProfile) -> None:
        self.profile = profile

    def invoke(self, request: ProviderRequest, cancellation: Event) -> ProviderResponse:
        del request, cancellation
        raise RuntimeError("provider_unavailable")


class DeterministicFakeProvider:
    def __init__(self, profile: ProviderProfile, output: dict[str, Any] | None = None) -> None:
        self.profile = profile
        self.output = output or {"status": "ok"}
        self.calls = 0

    def invoke(self, request: ProviderRequest, cancellation: Event) -> ProviderResponse:
        if cancellation.is_set():
            raise RuntimeError("provider_cancelled")
        self.calls += 1
        return ProviderResponse(
            request_id=request.request_id,
            final_text="deterministic fake response",
            structured_output=dict(self.output),
            provider_metadata={"thinking_mode_used": request.thinking_mode, "reasoning_content_stored": False},
        )


class OllamaProviderAdapter:
    """Configured future adapter; invocation remains explicit and never pulls models."""

    def __init__(self, profile: ProviderProfile, transport: Any | None = None) -> None:
        self.profile = profile
        self.transport = transport

    def invoke(self, request: ProviderRequest, cancellation: Event) -> ProviderResponse:
        if not self.profile.enabled or self.profile.health_status != CapabilityStatus.AVAILABLE:
            raise RuntimeError("provider_unavailable")
        if self.transport is None:
            raise RuntimeError("provider_not_configured")
        if cancellation.is_set():
            raise RuntimeError("provider_cancelled")
        # The injected transport owns the bounded local HTTP call. No model
        # substitution, pull, startup, or network discovery occurs here.
        result = self.transport(request, cancellation)
        return ProviderResponse(
            request_id=request.request_id,
            final_text=str(result.get("final_text") or ""),
            structured_output=result.get("structured_output"),
            provider_metadata={
                "thinking_mode_used": request.thinking_mode,
                "reasoning_content_stored": False,
            },
        )


__all__ = [
    "ProviderRequest", "ProviderResponse", "LocalModelProvider",
    "UnavailableProvider", "DeterministicFakeProvider", "OllamaProviderAdapter",
]
