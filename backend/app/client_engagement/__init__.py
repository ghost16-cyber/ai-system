"""Client engagement intake and immutable scope approval (Stage 10)."""

from backend.app.client_engagement.contracts import *
from backend.app.client_engagement.estimation import estimate_effort
from backend.app.client_engagement.evidence import collect_authorized_evidence
from backend.app.client_engagement.extraction import extract_requirements, parse_model_requirements
from backend.app.client_engagement.presentation import build_engagement_action, build_engagement_chat_run
from backend.app.client_engagement.scoping import build_scope_revision, classify_scope_change
from backend.app.client_engagement.service import EngagementError, EngagementService, detect_engagement_request, public_engagement, stage9_task_from_scope
from backend.app.client_engagement.workflow import transition_state

__all__ = [
    "EngagementError", "EngagementService", "build_engagement_action",
    "build_engagement_chat_run", "build_scope_revision", "classify_scope_change",
    "collect_authorized_evidence", "estimate_effort", "extract_requirements",
    "parse_model_requirements", "transition_state", "detect_engagement_request", "public_engagement", "stage9_task_from_scope",
]
