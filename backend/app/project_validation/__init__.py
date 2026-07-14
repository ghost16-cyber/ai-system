"""Stage 11: evidence-backed project validation and delivery readiness."""

from backend.app.project_validation.acceptance import classify_evaluation_method, evaluate_acceptance_criteria
from backend.app.project_validation.contracts import *
from backend.app.project_validation.inspection import build_deliverable_manifest
from backend.app.project_validation.limits import add_usage, enforce_budget, evaluate_budget
from backend.app.project_validation.presentation import build_validation_action, public_campaign
from backend.app.project_validation.quality import assess_quality
from backend.app.project_validation.regression import evaluate_regression
from backend.app.project_validation.scenarios import SCENARIOS as VALIDATION_SCENARIOS, get_scenario as get_validation_scenario
from backend.app.project_validation.service import ProjectValidationError, ProjectValidationService
from backend.app.project_validation.workspace import capture_snapshot, compare_snapshot, prepare_workspace, restore_snapshot

__all__ = [name for name in globals() if not name.startswith("_")]
