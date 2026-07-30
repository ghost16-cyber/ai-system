from __future__ import annotations

from pydantic import BaseModel, Field

from backend.app.local_ai.config import load_local_ai_configuration
from backend.app.slm.client import OllamaClient


class SLMModelConfig(BaseModel):
    name: str
    provider: str
    base_url: str
    role: str = "coordinator"
    max_context_tokens: int
    default_options: dict[str, float | int] = Field(
        default_factory=lambda: {"temperature": 0.1, "num_predict": 600}
    )


def list_slm_models() -> list[SLMModelConfig]:
    return [get_slm_model_config()]


def get_slm_model_config(
    model: str | None = None, *, base_url: str | None = None
) -> SLMModelConfig:
    configuration = load_local_ai_configuration()
    selected_model = model or configuration.coder_model
    if selected_model not in configuration.configured_models:
        raise ValueError("model_not_bound_to_canonical_local_ai_configuration")
    if base_url is not None and base_url.rstrip("/") != configuration.endpoint_identity:
        raise ValueError("endpoint_not_bound_to_canonical_local_ai_configuration")
    return SLMModelConfig(
        name=selected_model,
        provider=configuration.provider_type,
        base_url=configuration.endpoint_identity,
        max_context_tokens=configuration.maximum_context_tokens,
    )


def build_ollama_client(
    model: str | None = None,
    *,
    base_url: str | None = None,
    timeout_seconds: int | None = None,
) -> OllamaClient:
    configuration = load_local_ai_configuration()
    config = get_slm_model_config(model, base_url=base_url)
    return OllamaClient(
        model=config.name,
        base_url=config.base_url,
        timeout_seconds=timeout_seconds or configuration.generation_timeout_seconds,
        temperature=float(config.default_options.get("temperature", 0.1)),
        num_predict=min(
            int(config.default_options.get("num_predict", 600)),
            configuration.maximum_output_tokens,
        ),
    )


__all__ = [
    "SLMModelConfig",
    "build_ollama_client",
    "get_slm_model_config",
    "list_slm_models",
]
