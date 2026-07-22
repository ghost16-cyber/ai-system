from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, model_validator

from backend.app.project_control.contracts import StrictModel


DEFAULT_LOCAL_MODEL = "qwen2.5-coder:1.5b"
DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
_MODEL_TAG = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,99})?$"
)


class LocalAIConfigurationError(ValueError):
    """The bounded local-AI environment configuration is invalid."""


class LocalAIConfiguration(StrictModel):
    schema_version: Literal["astra.local-ai.configuration.v2"] = (
        "astra.local-ai.configuration.v2"
    )
    generation_enabled: bool = False
    provider_type: Literal["ollama", "unavailable", "fake"] = "ollama"
    endpoint_identity: str
    synthesis_model: str
    coder_model: str
    planner_model: str | None = None
    reviewer_model: str | None = None
    connection_timeout_seconds: int = Field(ge=1, le=30)
    generation_timeout_seconds: int = Field(ge=1, le=3600)
    maximum_context_tokens: int = Field(ge=256, le=1_048_576)
    maximum_output_tokens: int = Field(ge=1, le=65_536)
    allow_cpu_fallback: bool = False
    gpu_exclusive_concurrency: bool = True

    @model_validator(mode="after")
    def validate_endpoint_and_models(self) -> "LocalAIConfiguration":
        if self.provider_type == "ollama":
            _validate_endpoint(self.endpoint_identity)
        for role, tag in (
            ("synthesis", self.synthesis_model),
            ("coder", self.coder_model),
            ("planner", self.planner_model),
            ("reviewer", self.reviewer_model),
        ):
            if tag is not None:
                _validate_model_tag(tag, role=role)
        return self

    def model_for_role(self, role: str) -> str | None:
        return {
            "synthesis": self.synthesis_model,
            "coding": self.coder_model,
            "coder": self.coder_model,
            "planning": self.planner_model,
            "planner": self.planner_model,
            "review": self.reviewer_model,
            "reviewer": self.reviewer_model,
        }.get(role.strip().lower())

    @property
    def configured_models(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                model
                for model in (
                    self.synthesis_model,
                    self.coder_model,
                    self.planner_model,
                    self.reviewer_model,
                )
                if model is not None
            )
        )


def load_local_ai_configuration(
    environ: Mapping[str, str] | None = None,
) -> LocalAIConfiguration:
    env = os.environ if environ is None else environ
    provider = _value(env, "ASTRA_LOCAL_AI_PROVIDER", default="ollama").lower()
    endpoint = _canonical_endpoint(
        _first(
            env,
            "ASTRA_OLLAMA_ENDPOINT",
            "ASTRA_OLLAMA_BASE_URL",
            "ASTRA_SLM_BASE_URL",
            default=DEFAULT_OLLAMA_ENDPOINT,
        )
    )
    shared_model = _first(
        env,
        "ASTRA_LOCAL_AI_MODEL",
        "ASTRA_SLM_MODEL",
        default=DEFAULT_LOCAL_MODEL,
    )
    synthesis_model = _first(
        env,
        "ASTRA_LOCAL_AI_SYNTHESIS_MODEL",
        "ASTRA_PROJECT_SYNTHESIS_MODEL",
        default=shared_model,
    )
    coder_model = _value(env, "ASTRA_LOCAL_AI_CODER_MODEL", default=shared_model)
    planner_model = _optional_role(env, "ASTRA_LOCAL_AI_PLANNER_MODEL", shared_model)
    reviewer_model = _optional_role(env, "ASTRA_LOCAL_AI_REVIEWER_MODEL", shared_model)
    try:
        return LocalAIConfiguration(
            generation_enabled=_boolean(
                env,
                "ASTRA_LOCAL_AI_GENERATION_ENABLED"
                if "ASTRA_LOCAL_AI_GENERATION_ENABLED" in env
                else "ASTRA_SLM_ENABLED",
                default=True,
            ),
            provider_type=provider,
            endpoint_identity=endpoint,
            synthesis_model=synthesis_model,
            coder_model=coder_model,
            planner_model=planner_model,
            reviewer_model=reviewer_model,
            connection_timeout_seconds=_integer(
                env,
                ("ASTRA_LOCAL_AI_CONNECTION_TIMEOUT_SECONDS",),
                default=5,
                minimum=1,
                maximum=30,
            ),
            generation_timeout_seconds=_integer(
                env,
                (
                    "ASTRA_LOCAL_AI_GENERATION_TIMEOUT_SECONDS",
                    "ASTRA_PROJECT_SYNTHESIS_TIMEOUT_SECONDS",
                    "ASTRA_SLM_TIMEOUT_SECONDS",
                ),
                default=30,
                minimum=1,
                maximum=3600,
            ),
            maximum_context_tokens=_integer(
                env,
                ("ASTRA_LOCAL_AI_MAX_CONTEXT_TOKENS",),
                default=4096,
                minimum=256,
                maximum=1_048_576,
            ),
            maximum_output_tokens=_integer(
                env,
                ("ASTRA_LOCAL_AI_MAX_OUTPUT_TOKENS",),
                default=2048,
                minimum=1,
                maximum=65_536,
            ),
            allow_cpu_fallback=_boolean(
                env, "ASTRA_LOCAL_AI_ALLOW_CPU_FALLBACK", default=True
            ),
            gpu_exclusive_concurrency=_boolean(
                env, "ASTRA_LOCAL_AI_GPU_EXCLUSIVE", default=True
            ),
        )
    except (ValueError, TypeError) as exc:
        if isinstance(exc, LocalAIConfigurationError):
            raise
        raise LocalAIConfigurationError(str(exc)) from exc


def _canonical_endpoint(value: str) -> str:
    parsed = _validate_endpoint(value)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _validate_endpoint(value: str):
    if not value or len(value) > 2048 or any(char.isspace() for char in value):
        raise LocalAIConfigurationError("invalid_ollama_endpoint")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LocalAIConfigurationError("invalid_ollama_endpoint")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise LocalAIConfigurationError("invalid_ollama_endpoint") from exc
    return parsed


def _validate_model_tag(value: str, *, role: str) -> None:
    if not _MODEL_TAG.fullmatch(value):
        raise LocalAIConfigurationError(f"invalid_{role}_model_tag")


def _first(env: Mapping[str, str], *names: str, default: str) -> str:
    for name in names:
        if name in env:
            return _value(env, name)
    return default


def _value(env: Mapping[str, str], name: str, *, default: str | None = None) -> str:
    raw = env.get(name, default)
    if raw is None or not raw.strip():
        raise LocalAIConfigurationError(f"{name.lower()}_is_empty")
    if len(raw) > 2048:
        raise LocalAIConfigurationError(f"{name.lower()}_is_too_long")
    return raw.strip()


def _optional_role(env: Mapping[str, str], name: str, default: str) -> str | None:
    if name not in env:
        return default
    value = env[name].strip()
    if value.lower() in {"none", "unset", "disabled"}:
        return None
    if not value:
        raise LocalAIConfigurationError(f"{name.lower()}_is_empty")
    return value


def _integer(
    env: Mapping[str, str],
    names: tuple[str, ...],
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = next((env[name] for name in names if name in env), str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise LocalAIConfigurationError(f"invalid_{names[0].lower()}") from exc
    if not minimum <= value <= maximum:
        raise LocalAIConfigurationError(f"invalid_{names[0].lower()}")
    return value


def _boolean(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    if name not in env:
        return default
    normalized = env[name].strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise LocalAIConfigurationError(f"invalid_{name.lower()}")


__all__ = [
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_OLLAMA_ENDPOINT",
    "LocalAIConfiguration",
    "LocalAIConfigurationError",
    "load_local_ai_configuration",
]
