from __future__ import annotations

import json
import hashlib
import socket
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol
from urllib import error as url_error
from urllib import request as url_request


MAX_INSPECTION_RESPONSE_BYTES = 1_048_576
MAX_GENERATION_ENVELOPE_BYTES = 2_097_152
MAX_RESPONSE_SCHEMA_BYTES = 131_072
CancellationCheck = Callable[[], bool]


class ProviderErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_provider_request"
    UNREACHABLE = "provider_unreachable"
    TIMEOUT = "generation_timeout"
    CANCELLED = "generation_cancelled"
    REJECTED = "provider_rejected_request"
    MALFORMED_RESPONSE = "malformed_provider_response"
    # Phase 8C additions -- provider-registry-level and capability-declaration
    # failures, kept on this same enum rather than a parallel one since every
    # existing catch site already matches on `ProviderClientError`/`.code`.
    NOT_REGISTERED = "provider_not_registered"
    UNSUPPORTED_OPERATION = "unsupported_provider_operation"
    MODEL_NOT_LOADED = "model_not_loaded"


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
    exact_response_schema: dict[str, Any] | None = None
    exact_response_schema_hash: str | None = None


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
            "temperature": 0.0,
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
        if request.exact_response_schema is not None:
            payload["format"] = _validated_response_schema(request)
        elif request.exact_response_schema_hash is not None:
            raise ProviderClientError(
                ProviderErrorCode.INVALID_REQUEST,
                "The structured-generation schema binding is invalid.",
            )
        elif request.structured_json:
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


_UNGRAMMATABLE_SCHEMA_KEYWORDS = ("minLength", "maxLength")


def _strip_ungrammatable_bounds(node: Any) -> Any:
    """Drop string-length bound keywords the installed Ollama grammar compiler
    cannot handle (a very large ``maxLength`` reproducibly crashes its model
    runner: ``model runner has unexpectedly stopped``). Astra's own Pydantic
    model still enforces these bounds when it validates the parsed response,
    so removing them from the provider-facing grammar only affects what
    constrains decoding, not what Astra accepts."""
    if isinstance(node, dict):
        return {
            key: _strip_ungrammatable_bounds(value)
            for key, value in node.items()
            if key not in _UNGRAMMATABLE_SCHEMA_KEYWORDS
        }
    if isinstance(node, list):
        return [_strip_ungrammatable_bounds(item) for item in node]
    return node


def _validated_response_schema(
    request: ProviderGenerationRequest,
) -> dict[str, Any]:
    schema = request.exact_response_schema
    if not isinstance(schema, dict) or not schema:
        raise ProviderClientError(
            ProviderErrorCode.INVALID_REQUEST,
            "The structured-generation schema must be one bounded JSON object.",
        )
    try:
        encoded = json.dumps(
            schema,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        canonical = json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderClientError(
            ProviderErrorCode.INVALID_REQUEST,
            "The structured-generation schema is not canonical JSON.",
        ) from exc
    if not isinstance(canonical, dict) or len(encoded) > MAX_RESPONSE_SCHEMA_BYTES:
        raise ProviderClientError(
            ProviderErrorCode.INVALID_REQUEST,
            "The structured-generation schema exceeds its request bound.",
        )
    # The hash binds to Astra's exact, full validation contract -- computed
    # before stripping, so provenance/replay identity is unaffected by what
    # the provider transport can or cannot accept.
    schema_hash = hashlib.sha256(encoded).hexdigest()
    if request.exact_response_schema_hash != schema_hash:
        raise ProviderClientError(
            ProviderErrorCode.INVALID_REQUEST,
            "The structured-generation schema hash does not match its exact content.",
        )
    return _strip_ungrammatable_bounds(canonical)


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
    "MAX_RESPONSE_SCHEMA_BYTES",
]
