from __future__ import annotations

from typing import Any


SUPPORTED_BACKENDS = {"ollama", "llamacpp", "mock"}
DEFAULT_PROFILE_ID = "qwen25_coder_15b_ollama"

SLM_PROFILES: dict[str, dict[str, Any]] = {
    "qwen25_coder_15b_ollama": {
        "profile_id": "qwen25_coder_15b_ollama",
        "model_name": "qwen2.5-coder:1.5b",
        "backend": "ollama",
        "purpose": "coding chat, intent understanding, planning explanations",
        "context_window": 32768,
        "recommended_quantization": "q4_K_M or Ollama default small quant",
        "gpu_safe": True,
        "estimated_vram_tier": "low",
    },
    "qwen25_15b_ollama": {
        "profile_id": "qwen25_15b_ollama",
        "model_name": "qwen2.5:1.5b",
        "backend": "ollama",
        "purpose": "general chat and intent understanding",
        "context_window": 32768,
        "recommended_quantization": "q4_K_M or Ollama default small quant",
        "gpu_safe": True,
        "estimated_vram_tier": "low",
    },
    "tinyllama_mock": {
        "profile_id": "tinyllama_mock",
        "model_name": "tinyllama:1.1b",
        "backend": "mock",
        "purpose": "safe deterministic fallback when local model runtime is unavailable",
        "context_window": 2048,
        "recommended_quantization": "q4",
        "gpu_safe": True,
        "estimated_vram_tier": "very_low",
    },
}

_selected_profile_id = DEFAULT_PROFILE_ID


def list_slm_profiles() -> dict[str, Any]:
    return {
        "profiles": list(SLM_PROFILES.values()),
        "count": len(SLM_PROFILES),
        "supported_backends": sorted(SUPPORTED_BACKENDS),
        "default_profile_id": DEFAULT_PROFILE_ID,
    }


def get_selected_slm_profile() -> dict[str, Any]:
    profile = SLM_PROFILES[_selected_profile_id]
    return {
        "selected_profile_id": _selected_profile_id,
        "profile": profile,
        "loaded": False,
        "prompts_executed": False,
        "advisory_only": True,
    }


def select_slm_profile(profile_id: str) -> dict[str, Any]:
    global _selected_profile_id
    if profile_id not in SLM_PROFILES:
        return {
            "selected": False,
            "reason": f"Unknown SLM profile: {profile_id}",
            "available_profile_ids": sorted(SLM_PROFILES),
        }
    _selected_profile_id = profile_id
    return {
        "selected": True,
        **get_selected_slm_profile(),
        "model_loaded": False,
        "tools_authorized": False,
        "runtime_authorized": False,
    }
