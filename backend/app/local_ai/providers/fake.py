from __future__ import annotations

from datetime import datetime, timezone

from backend.app.local_ai.config import LocalAIConfiguration
from backend.app.local_ai.contracts import Capability, CapabilityStatus
from backend.app.local_ai.provider import (
    CancellationCheck,
    ProviderClientError,
    ProviderErrorCode,
    ProviderGenerationRequest,
    ProviderGenerationResponse,
    ProviderInspection,
)
from backend.app.local_ai.providers.base import ProviderCapabilityDeclaration


class FakeDeterministicProvider:
    """Offline, no-network, fully deterministic provider adapter.

    Backs the `fake-deterministic` model/provider profiles used by unit
    tests, deterministic replay, and any no-network test suite. It never
    performs I/O and never returns anything but the exact configured
    response -- there is no randomness to weaken.
    """

    def __init__(
        self,
        *,
        provider_id: str = "fake-deterministic",
        model_tag: str = "fake:deterministic",
        response: str = '{"response": "deterministic fake response"}',
    ) -> None:
        self.provider_id = provider_id
        self.capabilities = ProviderCapabilityDeclaration(
            generation_supported=True,
            structured_output_supported=True,
            cancellation_supported=True,
            streaming_supported=False,
            model_discovery_supported=True,
            loaded_model_discovery_supported=True,
            gpu_supported=False,
            cpu_supported=True,
        )
        self._model_tag = model_tag
        self._response = response
        self.inspect_calls = 0
        self.generate_calls = 0

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        del timeout_seconds
        self.inspect_calls += 1
        return ProviderInspection(
            provider_version="fake-1",
            installed_models=(self._model_tag,),
            loaded_models=(self._model_tag,),
        )

    def generate(
        self,
        request: ProviderGenerationRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> ProviderGenerationResponse:
        self.generate_calls += 1
        if cancelled is not None and cancelled():
            raise ProviderClientError(
                ProviderErrorCode.CANCELLED, "The local model request was cancelled."
            )
        return ProviderGenerationResponse(
            model=request.model,
            response=self._response,
            metadata={"prompt_eval_count": 1, "eval_count": 1},
        )

    def probe_capability(self, configuration: LocalAIConfiguration) -> Capability:
        del configuration
        return Capability(
            capability_id=f"provider:{self.provider_id}",
            status=CapabilityStatus.AVAILABLE,
            probed_at=datetime.now(timezone.utc),
            provenance={"kind": "fake_deterministic", "network_probe": False},
        )


__all__ = ["FakeDeterministicProvider"]
