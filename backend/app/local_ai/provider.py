from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol
from urllib import error as url_error
from urllib import request as url_request


MAX_INSPECTION_RESPONSE_BYTES = 1_048_576
MAX_GENERATION_ENVELOPE_BYTES = 2_097_152
CancellationCheck = Callable[[], bool]


class ProviderErrorCode(StrEnum):
    UNREACHABLE = "provider_unreachable"
    TIMEOUT = "generation_timeout"
    CANCELLED = "generation_cancelled"
    REJECTED = "provider_rejected_request"
    MALFORMED_RESPONSE = "malformed_provider_response"


class ProviderClientError(RuntimeError):
    def __init__(
        self,
        code: ProviderErrorCode,
        safe_message: str,
        *,
        diagnostic: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.safe_message = safe_message
        self.diagnostic = diagnostic or {}
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class ProviderInspection:
    provider_version: str | None
    installed_models: tuple[str, ...]
    loaded_models: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderGenerationRequest:
    model: str
    system_instruction: str
    prompt: str
    timeout_seconds: int
    maximum_output_tokens: int
    temperature: float = 0.0
    top_p: float = 1.0
    seed: int | None = None
    structured_json: bool = True


@dataclass(frozen=True, slots=True)
class ProviderGenerationResponse:
    model: str
    response: str
    metadata: dict[str, int] = field(default_factory=dict)


class LocalModelProviderClient(Protocol):
    def inspect(self, *, timeout_seconds: int) -> ProviderInspection: ...

    def generate(
        self,
        request: ProviderGenerationRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> ProviderGenerationResponse: ...


class OllamaProviderClient:
    """The sole bounded stdlib HTTP boundary for local Ollama operations."""

    provider_identity = "ollama"

    def __init__(self, endpoint_identity: str) -> None:
        self.endpoint_identity = endpoint_identity.rstrip("/")

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        version = self._get_json("/api/version", timeout_seconds, allow_missing=True)
        tags = self._get_json("/api/tags", timeout_seconds)
        loaded = self._get_json("/api/ps", timeout_seconds, allow_missing=True)
        return ProviderInspection(
            provider_version=(
                str(version["version"])
                if isinstance(version.get("version"), str)
                else None
            ),
            installed_models=_model_names(tags),
            loaded_models=_model_names(loaded),
        )

    def generate(
        self,
        request: ProviderGenerationRequest,
        *,
        cancelled: CancellationCheck | None = None,
    ) -> ProviderGenerationResponse:
        _raise_if_cancelled(cancelled)
        options: dict[str, int | float] = {
            "temperature": request.temperature,
            "top_p": request.top_p,
            "num_predict": request.maximum_output_tokens,
        }
        if request.seed is not None:
            options["seed"] = request.seed
        payload: dict[str, Any] = {
            "model": request.model,
            "system": request.system_instruction,
            "prompt": request.prompt,
            "stream": False,
            "options": options,
        }
        if request.structured_json:
            payload["format"] = "json"
        parsed = self._request_json(
            "/api/generate",
            payload,
            request.timeout_seconds,
            maximum_bytes=MAX_GENERATION_ENVELOPE_BYTES,
        )
        _raise_if_cancelled(cancelled)
        model = parsed.get("model")
        response = parsed.get("response")
        if not isinstance(model, str) or not model or not isinstance(response, str):
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model returned a malformed response envelope.",
            )
        metadata: dict[str, int] = {}
        for field_name in (
            "prompt_eval_count",
            "eval_count",
            "prompt_eval_duration",
            "eval_duration",
        ):
            value = parsed.get(field_name)
            if value is not None:
                if not isinstance(value, int) or value < 0:
                    raise ProviderClientError(
                        ProviderErrorCode.MALFORMED_RESPONSE,
                        "The local model returned invalid evaluation metadata.",
                    )
                metadata[field_name] = value
        return ProviderGenerationResponse(model=model, response=response, metadata=metadata)

    def _get_json(
        self, path: str, timeout_seconds: int, *, allow_missing: bool = False
    ) -> dict[str, Any]:
        try:
            return self._request_json(
                path,
                None,
                timeout_seconds,
                maximum_bytes=MAX_INSPECTION_RESPONSE_BYTES,
            )
        except ProviderClientError as exc:
            if allow_missing and exc.code == ProviderErrorCode.REJECTED:
                return {}
            raise

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
                ProviderErrorCode.TIMEOUT,
                "The local model request timed out.",
            ) from exc
        except url_error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderClientError(
                    ProviderErrorCode.TIMEOUT,
                    "The local model request timed out.",
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


def _model_names(payload: dict[str, Any]) -> tuple[str, ...]:
    models = payload.get("models", [])
    if not isinstance(models, list):
        raise ProviderClientError(
            ProviderErrorCode.MALFORMED_RESPONSE,
            "The local model inventory is malformed.",
        )
    names: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model inventory is malformed.",
            )
        name = item.get("name") or item.get("model")
        if not isinstance(name, str) or not name or len(name) > 300:
            raise ProviderClientError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "The local model inventory contains an invalid model identity.",
            )
        names.append(name)
    return tuple(dict.fromkeys(names))


def _raise_if_cancelled(cancelled: CancellationCheck | None) -> None:
    if cancelled is not None and cancelled():
        raise ProviderClientError(
            ProviderErrorCode.CANCELLED,
            "The local model request was cancelled.",
        )


__all__ = [
    "CancellationCheck",
    "LocalModelProviderClient",
    "OllamaProviderClient",
    "ProviderClientError",
    "ProviderErrorCode",
    "ProviderGenerationRequest",
    "ProviderGenerationResponse",
    "ProviderInspection",
]
