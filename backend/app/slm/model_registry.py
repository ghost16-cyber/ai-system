from __future__ import annotations

from pydantic import BaseModel, Field

from backend.app.slm.client import OllamaClient


class SLMModelConfig(BaseModel):
    name: str
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    role: str = "coordinator"
    max_context_tokens: int = 4096
    default_options: dict[str, float | int] = Field(
        default_factory=lambda: {
            "temperature": 0.1,
            "num_predict": 600,
        }
    )


DEFAULT_SLM_MODELS: dict[str, SLMModelConfig] = {
    "qwen2.5-coder:1.5b": SLMModelConfig(name="qwen2.5-coder:1.5b"),
}


def list_slm_models() -> list[SLMModelConfig]:
    return list(DEFAULT_SLM_MODELS.values())


def get_slm_model_config(
    model: str = "qwen2.5-coder:1.5b",
    *,
    base_url: str | None = None,
) -> SLMModelConfig:
    config = DEFAULT_SLM_MODELS.get(model) or SLMModelConfig(name=model)
    if base_url is not None:
        return config.model_copy(update={"base_url": base_url})
    return config


def build_ollama_client(
    model: str = "qwen2.5-coder:1.5b",
    *,
    base_url: str | None = None,
    timeout_seconds: int = 90,
) -> OllamaClient:
    config = get_slm_model_config(model, base_url=base_url)
    return OllamaClient(
        model=config.name,
        base_url=config.base_url,
        timeout_seconds=timeout_seconds,
        temperature=float(config.default_options.get("temperature", 0.1)),
        num_predict=int(config.default_options.get("num_predict", 600)),
    )
