from backend.app.project_analysis.audit import analysis_audit_metadata
from backend.app.project_analysis.dependencies import build_dependency_graph
from backend.app.project_analysis.impact import analyze_impact
from backend.app.project_analysis.inventory import build_project_index, file_hashes, public_index
from backend.app.project_analysis.models import INDEX_VERSION, ProjectAnalysisError
from backend.app.project_analysis.planner import build_analysis_plan
from backend.app.project_analysis.references import search_references
from backend.app.project_analysis.synthesis import synthesize_project_patch, validate_contract
from backend.app.project_analysis.symbols import analyze_source
from backend.app.project_analysis.validation import prevalidate_virtual_files
from backend.app.project_analysis.state_manifest import (
    IncompleteProjectManifestError, ProjectManifestError, ProjectStateManifest,
    assert_manifest_fresh, build_project_state_manifest,
)

__all__ = [
    "INDEX_VERSION", "ProjectAnalysisError", "analysis_audit_metadata", "analyze_impact",
    "analyze_source", "build_analysis_plan", "build_dependency_graph", "build_project_index",
    "file_hashes", "prevalidate_virtual_files", "public_index", "search_references",
    "synthesize_project_patch", "validate_contract",
    "IncompleteProjectManifestError", "ProjectManifestError", "ProjectStateManifest",
    "assert_manifest_fresh", "build_project_state_manifest",
]
