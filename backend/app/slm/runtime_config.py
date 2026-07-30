from __future__ import annotations

from typing import Any

from backend.app.local_ai.config import load_local_ai_configuration


SUPPORTED_BACKENDS = {"ollama", "mock"}
DEFAULT_PROFILE_ID = "configured_local_ai"
_selected_profile_id = DEFAULT_PROFILE_ID


def _profiles() -> dict[str, dict[str, Any]]:
    configuration = load_local_ai_configuration()
    profiles = {
        DEFAULT_PROFILE_ID: {
            "profile_id": DEFAULT_PROFILE_ID,
            "model_name": configuration.coder_model,
            "backend": configuration.provider_type,
            "purpose": "coding chat, intent understanding, planning explanations",
            "context_window": configuration.maximum_context_tokens,
            "gpu_safe": True,
            "estimated_vram_tier": "configured",
            "configuration_authority": configuration.schema_version,
            "no_auto_start": True,
            "no_auto_pull": True,
        },
    }
    for profile_id, role, model in (
        ("configured_planner", "planning explanations", configuration.planner_model),
        ("configured_reviewer", "review explanations", configuration.reviewer_model),
    ):
        if model is not None:
            profiles[profile_id] = {
                "profile_id": profile_id,
                "model_name": model,
                "backend": configuration.provider_type,
                "purpose": role,
                "context_window": configuration.maximum_context_tokens,
                "gpu_safe": True,
                "estimated_vram_tier": "configured",
                "configuration_authority": configuration.schema_version,
                "no_auto_start": True,
                "no_auto_pull": True,
            }
    profiles["deterministic_mock"] = {
        "profile_id": "deterministic_mock",
        "model_name": "fake:deterministic",
        "backend": "mock",
        "purpose": "explicit deterministic test adapter",
        "context_window": 2048,
        "gpu_safe": True,
        "estimated_vram_tier": "none",
        "configuration_authority": configuration.schema_version,
    }
    return profiles


def list_slm_profiles() -> dict[str, Any]:
    profiles = _profiles()
    return {
        "profiles": list(profiles.values()),
        "count": len(profiles),
        "supported_backends": sorted(SUPPORTED_BACKENDS),
        "default_profile_id": DEFAULT_PROFILE_ID,
    }


def get_selected_slm_profile() -> dict[str, Any]:
    profiles = _profiles()
    selected = _selected_profile_id if _selected_profile_id in profiles else DEFAULT_PROFILE_ID
    return {
        "selected_profile_id": selected,
        "profile": profiles[selected],
        "loaded": False,
        "prompts_executed": False,
        "advisory_only": True,
    }


def select_slm_profile(profile_id: str) -> dict[str, Any]:
    global _selected_profile_id
    profiles = _profiles()
    if profile_id not in profiles:
        return {
            "selected": False,
            "reason": f"Unknown SLM profile: {profile_id}",
            "available_profile_ids": sorted(profiles),
        }
    _selected_profile_id = profile_id
    return {
        "selected": True,
        **get_selected_slm_profile(),
        "model_loaded": False,
        "tools_authorized": False,
        "runtime_authorized": False,
    }


__all__ = [
    "DEFAULT_PROFILE_ID",
    "SUPPORTED_BACKENDS",
    "get_selected_slm_profile",
    "list_slm_profiles",
    "select_slm_profile",
]
