from __future__ import annotations

from datetime import datetime, timezone

from backend.app.local_ai.config import LocalAIConfiguration
from backend.app.local_ai.contracts import CapabilityStatus, OllamaCapability
from backend.app.local_ai.provider import (
    CancellationCheck,
    OllamaProviderClient,
    ProviderClientError,
    ProviderGenerationRequest,
    ProviderGenerationResponse,
    ProviderInspection,
)
from backend.app.local_ai.providers.base import ProviderCapabilityDeclaration


class OllamaProviderAdapter:
    """Canonical adapter for the Ollama runtime.

    Wraps (composes, never subclasses) the existing `OllamaProviderClient`
    -- all endpoint handling, HTTP transport, `/api/tags`/`/api/ps`/
    `/api/generate` calls, and response parsing stay exactly where they
    already lived. This adapter only adds the provider-neutral surface
    (`provider_id`, `capabilities`, `probe_capability`) on top; `inspect`/
    `generate` are pure delegation so any existing test that monkeypatches
    `OllamaProviderClient.inspect`/`.generate` at the class level keeps
    working unchanged (it patches the same class this adapter calls into).
    """

    def __init__(
        self,
        *,
        endpoint_identity: str,
        provider_id: str = "ollama-local",
        client: OllamaProviderClient | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.endpoint_identity = endpoint_identity.rstrip("/")
        self._client = client or OllamaProviderClient(endpoint_identity)
        self.capabilities = ProviderCapabilityDeclaration(
            generation_supported=True,
            structured_output_supported=True,
            cancellation_supported=False,
            streaming_supported=False,
            model_discovery_supported=True,
            loaded_model_discovery_supported=True,
            gpu_supported=True,
            cpu_supported=True,
        )

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        return self._client.inspect(timeout_seconds=timeout_seconds)

    def generate(
        self,
        request: ProviderGenerationRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> ProviderGenerationResponse:
        return self._client.generate(request, cancelled=cancelled)

    def probe_capability(self, configuration: LocalAIConfiguration) -> OllamaCapability:
        """Bounded, read-only Ollama capability probe -- never starts the
        daemon or pulls a model. Preserves the exact `OllamaCapability`
        shape/semantics the UI and existing tests already depend on."""
        now = datetime.now(timezone.utc)
        try:
            inspection = self._client.inspect(
                timeout_seconds=configuration.connection_timeout_seconds
            )
        except ProviderClientError:
            return OllamaCapability(
                capability_id="ollama",
                status=CapabilityStatus.UNAVAILABLE,
                endpoint=configuration.endpoint_identity,
                configured_models=configuration.configured_models,
                installed_models=(),
                loaded_models=(),
                provider_reachable=False,
                configured_model_missing=False,
                probed_at=now,
                reason="provider_unreachable",
                provenance={
                    "configuration_authority": configuration.schema_version,
                    "network_probe": True,
                    "no_auto_start": True,
                    "no_auto_pull": True,
                },
            )
        configured_missing = any(
            model not in inspection.installed_models
            for model in configuration.configured_models
        )
        return OllamaCapability(
            capability_id="ollama",
            status=(
                CapabilityStatus.UNAVAILABLE
                if configured_missing
                else CapabilityStatus.AVAILABLE
            ),
            endpoint=configuration.endpoint_identity,
            configured_models=configuration.configured_models,
            installed_models=inspection.installed_models,
            loaded_models=inspection.loaded_models,
            provider_reachable=True,
            configured_model_missing=configured_missing,
            probed_at=now,
            reason="configured_model_missing" if configured_missing else None,
            provenance={
                "configuration_authority": configuration.schema_version,
                "network_probe": True,
                "no_auto_start": True,
                "no_auto_pull": True,
            },
        )


__all__ = ["OllamaProviderAdapter"]
