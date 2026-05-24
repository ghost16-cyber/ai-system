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
        name="get_metrics",
        description="Read aggregate analysis, validation, and feedback metrics.",
        input_schema={},
        read_only=True,
        execution="synchronous",
    ),
)


def get_tool_metadata() -> list[ToolMetadataResponse]:
    return [metadata.model_copy(deep=True) for metadata in TOOL_METADATA]
