from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from typing import Any
from urllib import error as url_error
from urllib import request as url_request

from backend.app.local_ai.config import LocalAIConfiguration
from backend.app.local_ai.contracts import CapabilityStatus, LlamaCppCapability
from backend.app.local_ai.provider import (
    CancellationCheck,
    ProviderClientError,
    ProviderErrorCode,
    ProviderGenerationRequest,
    ProviderGenerationResponse,
    ProviderInspection,
)
from backend.app.local_ai.providers.base import ProviderCapabilityDeclaration


MAX_INSPECTION_RESPONSE_BYTES = 1_048_576
MAX_GENERATION_ENVELOPE_BYTES = 2_097_152


class LlamaCppProviderClient:
    """The sole bounded stdlib HTTP boundary for llama-server operations.

    Treats llama-server as an independently managed runtime: never starts,
    stops, compiles, or downloads anything. Uses the OpenAI-compatible
    `/v1/models` (health + loaded-model discovery) and `/v1/chat/completions`
    (generation) endpoints, which are the two most consistently documented
    across llama.cpp server versions. Whether a given llama-server build also
    supports strict `response_format: json_schema` decoding is version
    dependent and not assumed here -- see docs/astra-phase8c-canonical-local-ai-runtime.md.
    """

    provider_identity = "llama_cpp"

    def __init__(self, endpoint_identity: str) -> None:
        self.endpoint_identity = endpoint_identity.rstrip("/")

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        models = self._get_json("/v1/models", timeout_seconds)
        loaded = _model_ids(models)
        return ProviderInspection(
            provider_version=None,
            installed_models=(),
            loaded_models=loaded,
        )

    def generate(
        self,
        request: ProviderGenerationRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> ProviderGenerationResponse:
        _raise_if_cancelled(cancelled)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_instruction},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": request.temperature,
            "top_p": request.top_p,
            "max_tokens": request.maximum_output_tokens,
            "stream": False,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.exact_response_schema is not None:
            # Basic JSON-object mode: widely supported across llama-server
            # builds. Strict `json_schema`-constrained decoding is not
            # assumed here -- see module docstring.
            payload["response_format"] = {"type": "json_object"}
        elif request.structured_json:
            payload["response_format"] = {"type": "json_object"}
        parsed = self._request_json(
            "/v1/chat/completions",
            payload,
            request.timeout_seconds,
            maximum_bytes=MAX_GENERATION_ENVELOPE_BYTES,
        )
        _raise_if_cancelled(cancelled)
        model = parsed.get("model")
        choices = parsed.get("choices")
        if (
            not isinstance(model, str)
            or not model
            or not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model returned a malformed response envelope.",
            )
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model returned a malformed response envelope.",
            )
        metadata: dict[str, int] = {}
        usage = parsed.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            if isinstance(prompt_tokens, int) and prompt_tokens >= 0:
                metadata["prompt_eval_count"] = prompt_tokens
            if isinstance(completion_tokens, int) and completion_tokens >= 0:
                metadata["eval_count"] = completion_tokens
        return ProviderGenerationResponse(model=model, response=content, metadata=metadata)

    def _get_json(self, path: str, timeout_seconds: int) -> dict[str, Any]:
        return self._request_json(
            path, None, timeout_seconds, maximum_bytes=MAX_INSPECTION_RESPONSE_BYTES
        )

    def _request_json(
        self,
        path: str,
        payload: dict[str, Any] | None,
        timeout_seconds: int,
        *,
        maximum_bytes: int,
    ) -> dict[str, Any]:
        encoded = (
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        http_request = url_request.Request(
            f"{self.endpoint_identity}{path}",
            data=encoded,
            headers={"Content-Type": "application/json"} if encoded else {},
            method="POST" if encoded else "GET",
        )
        try:
            with url_request.urlopen(http_request, timeout=timeout_seconds) as response:
                body = response.read(maximum_bytes + 1)
        except url_error.HTTPError as exc:
            raise ProviderClientError(
                ProviderErrorCode.REJECTED,
                "The local model provider rejected the request.",
                diagnostic={"http_status": int(exc.code)},
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderClientError(
                ProviderErrorCode.TIMEOUT, "The local model request timed out."
            ) from exc
        except url_error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderClientError(
                    ProviderErrorCode.TIMEOUT, "The local model request timed out."
                ) from exc
            raise ProviderClientError(
                ProviderErrorCode.UNREACHABLE,
                "The configured local model provider is unreachable.",
            ) from exc
        except OSError as exc:
            raise ProviderClientError(
                ProviderErrorCode.UNREACHABLE,
                "The configured local model provider is unreachable.",
            ) from exc
        if len(body) > maximum_bytes:
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model response exceeded the bounded envelope size.",
            )
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model returned malformed JSON transport data.",
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model response envelope must be an object.",
            )
        return parsed


def _model_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    data = payload.get("data", [])
    if not isinstance(data, list):
        raise ProviderClientError(
            ProviderErrorCode.MALFORMED_RESPONSE,
            "The local model inventory is malformed.",
        )
    ids: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model inventory is malformed.",
            )
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id or len(model_id) > 300:
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model inventory contains an invalid model identity.",
            )
        ids.append(model_id)
    return tuple(dict.fromkeys(ids))


def _raise_if_cancelled(cancelled: CancellationCheck | None) -> None:
    if cancelled is not None and cancelled():
        raise ProviderClientError(
            ProviderErrorCode.CANCELLED, "The local model request was cancelled."
        )


class LlamaCppProviderAdapter:
    """Canonical adapter for an externally managed llama-server process.

    Configuration-driven only: endpoint and configured model identity come
    from `LocalAIConfiguration`/`ModelProfile`, never a hard-coded path.
    Never starts, stops, compiles llama.cpp, downloads a GGUF file, or adds
    native Python bindings -- this is an HTTP client only.
    """

    def __init__(
        self,
        *,
        endpoint_identity: str,
        configured_model: str | None = None,
        provider_id: str = "llama-cpp-local",
        client: LlamaCppProviderClient | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.endpoint_identity = endpoint_identity.rstrip("/")
        self.configured_model = configured_model
        self._client = client or LlamaCppProviderClient(endpoint_identity)
        self.capabilities = ProviderCapabilityDeclaration(
            generation_supported=True,
            structured_output_supported=True,
            cancellation_supported=False,
            streaming_supported=False,
            # llama-server has no "installed but not loaded" registry the
            # way Ollama does -- only the currently loaded model is ever
            # knowable, so installed-model discovery is not claimed.
            model_discovery_supported=False,
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

    def probe_capability(self, configuration: LocalAIConfiguration) -> LlamaCppCapability:
        """Bounded, read-only probe. Never starts llama-server or loads a model."""
        now = datetime.now(timezone.utc)
        try:
            inspection = self._client.inspect(
                timeout_seconds=configuration.connection_timeout_seconds
            )
        except ProviderClientError:
            return LlamaCppCapability(
                capability_id="llama_cpp",
                status=CapabilityStatus.UNAVAILABLE,
                endpoint=self.endpoint_identity,
                configured_model=self.configured_model,
                loaded_models=(),
                provider_reachable=False,
                configured_model_missing=False,
                probed_at=now,
                reason="provider_unreachable",
                provenance={"network_probe": True, "no_auto_start": True, "no_auto_download": True},
            )
        configured_missing = (
            self.configured_model is not None
            and self.configured_model not in inspection.loaded_models
        )
        return LlamaCppCapability(
            capability_id="llama_cpp",
            status=(
                CapabilityStatus.UNAVAILABLE
                if configured_missing
                else CapabilityStatus.AVAILABLE
            ),
            endpoint=self.endpoint_identity,
            configured_model=self.configured_model,
            loaded_models=inspection.loaded_models,
            provider_reachable=True,
            configured_model_missing=configured_missing,
            probed_at=now,
            reason="configured_model_not_loaded" if configured_missing else None,
            provenance={"network_probe": True, "no_auto_start": True, "no_auto_download": True},
        )


__all__ = [
    "LlamaCppCapability",
    "LlamaCppProviderAdapter",
    "LlamaCppProviderClient",
]
