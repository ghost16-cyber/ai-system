from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.app.local_ai.config import LocalAIConfiguration
from backend.app.local_ai.contracts import Capability
from backend.app.local_ai.provider import (
    CancellationCheck,
    ProviderGenerationRequest,
    ProviderGenerationResponse,
    ProviderInspection,
)


@dataclass(frozen=True, slots=True)
class ProviderCapabilityDeclaration:
    """Explicit capability flags for one provider adapter.

    Higher layers read these instead of branching on `provider_id`/
    `provider_type` strings. An unsupported operation must fail explicitly
    (see `providers.errors`) rather than being silently faked.
    """

    generation_supported: bool = True
    structured_output_supported: bool = True
    cancellation_supported: bool = False
    streaming_supported: bool = False
    model_discovery_supported: bool = True
    loaded_model_discovery_supported: bool = True
    gpu_supported: bool = True
    cpu_supported: bool = True


class CanonicalProvider(Protocol):
    """Provider-neutral runtime contract every adapter satisfies.

    Deliberately shaped as a superset of the existing `LocalModelProviderClient`
    Protocol (`local_ai/provider.py`): `inspect`/`generate` keep the exact same
    signatures, so any `CanonicalProvider` instance can be handed directly to
    `LocalGenerationGateway` as its `provider_client` with zero adaptation.
    `provider_id`/`capabilities`/`probe_capability` are the additional surface
    the registry and `LocalAIService`'s capability reporting use.
    """

    provider_id: str
    capabilities: ProviderCapabilityDeclaration

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection: ...

    def generate(
        self,
        request: ProviderGenerationRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> ProviderGenerationResponse: ...

    def probe_capability(self, configuration: LocalAIConfiguration) -> Capability:
        """Return this provider's own capability record for a fresh snapshot.

        Read-only: never starts, stops, installs, or downloads anything.
        """
        ...


__all__ = ["CanonicalProvider", "ProviderCapabilityDeclaration"]
