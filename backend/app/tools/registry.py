from __future__ import annotations

from backend.app.schemas.api import ToolMetadataResponse


TOOL_METADATA: tuple[ToolMetadataResponse, ...] = (
    ToolMetadataResponse(
        name="analyze_code",
        description="Analyze a submitted Python source snippet using deterministic rules.",
        input_schema={
            "code": "string",
            "language": "python",
            "filename": "string | null",
        },
        read_only=True,
        execution="synchronous",
    ),
    ToolMetadataResponse(
        name="get_rules",
        description="List supported deterministic analysis rules and safe-fix availability.",
        input_schema={},
        read_only=True,
        execution="synchronous",
    ),
    ToolMetadataResponse(
        name="analyze_file",
        description="Analyze a Python file located within the configured workspace root.",
        input_schema={"path": "workspace-relative .py path"},
        read_only=True,
        execution="synchronous",
    ),
    ToolMetadataResponse(
        name="analyze_project",
        description="Queue deterministic analysis of Python files in a workspace project.",
        input_schema={"path": "workspace-relative directory path"},
        read_only=True,
        execution="job_backed",
    ),
    ToolMetadataResponse(
        name="get_metrics",
        description="Read aggregate analysis, validation, and feedback metrics.",
        input_schema={},
        read_only=True,
        execution="synchronous",
    ),
    ToolMetadataResponse(
        name="analyze_ai_hardware",
        description=(
            "Detect local CPU, RAM, GPU, VRAM, storage, and PyTorch/CUDA "
            "status, then return low-VRAM training recommendations."
        ),
        input_schema={},
        read_only=True,
        execution="synchronous",
    ),
    ToolMetadataResponse(
        name="get_runtime_context",
        description=(
            "Build Astra's local runtime context for a task, including hardware, "
            "installed tools, capabilities, and safe execution settings."
        ),
        input_schema={"task": "string | optional"},
        read_only=True,
        execution="synchronous",
    ),
    ToolMetadataResponse(
        name="validate_runtime_plan",
        description=(
            "Deterministically allow, downgrade, or block a proposed AI runtime "
            "plan using the detected machine policy."
        ),
        input_schema={
            "task": "string",
            "requested_plan": "object",
        },
        read_only=True,
        execution="synchronous",
    ),
    ToolMetadataResponse(
        name="authorize_runtime_plan",
        description=(
            "Confirm that a proposed AI workload plan matches the active "
            "runtime-policy-approved or downgraded plan."
        ),
        input_schema={"plan": "object | optional"},
        read_only=True,
        execution="synchronous",
    ),
    ToolMetadataResponse(
        name="build_execution_profile",
        description=(
            "Compile a validated runtime plan into concrete task execution "
            "settings for the detected machine."
        ),
        input_schema={"task": "string", "requested_plan": "object"},
        read_only=True,
        execution="synchronous",
    ),
    ToolMetadataResponse(
        name="orchestrate",
        description=(
            "Queue a controlled SLM-ready task loop with advisors, safe tools, "
            "validators, and redacted trace output."
        ),
        input_schema={
            "goal": "string",
            "path": "workspace-relative directory path",
            "allow_edits": "boolean",
            "allow_tests": "boolean",
            "max_steps": "integer",
            "proposer": "scripted | slm",
            "slm_model": "string",
            "slm_base_url": "string",
        },
        read_only=False,
        execution="job_backed",
    ),
)


def get_tool_metadata() -> list[ToolMetadataResponse]:
    return [metadata.model_copy(deep=True) for metadata in TOOL_METADATA]
