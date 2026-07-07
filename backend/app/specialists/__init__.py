from backend.app.specialists.dashboard import build_specialist_dashboard
from backend.app.specialists.dataset_loader import (
    load_jsonl_dataset,
    load_specialist_eval_dataset,
    validate_dataset_row,
)
from backend.app.specialists.dataset_registry import (
    approve_dataset,
    archive_dataset,
    get_dataset,
    list_datasets,
    register_dataset,
)
from backend.app.specialists.error_classifier import classify_error
from backend.app.specialists.evaluation import evaluate_examples, evaluate_specialist_dataset
from backend.app.specialists.feedback_logger import append_specialist_feedback
from backend.app.specialists.intent_classifier import predict_intent
from backend.app.specialists.metrics import (
    calculate_accuracy,
    calculate_confusion_matrix,
    calculate_label_counts,
    summarize_failures,
)
from backend.app.specialists.model_audit_logger import (
    append_model_audit_event,
    load_model_audit_events,
)
from backend.app.specialists.model_evaluation_report import build_model_evaluation_report
from backend.app.specialists.model_quality_gate import evaluate_quality_gate
from backend.app.specialists.model_quality_gate import evaluate_promotion_quality_gate
from backend.app.specialists.model_promoter import (
    deactivate_model,
    promote_model,
    reject_model,
)
from backend.app.specialists.model_rollback import rollback_specialist_model
from backend.app.specialists.model_store import (
    build_model_metadata,
    deactivate_specialist_model,
    find_specialist_model,
    lifecycle_artifact_path,
    list_specialist_models,
    load_specialist_model,
    promote_specialist_model,
    reject_specialist_model,
    save_specialist_model,
    specialist_artifact_path,
    validate_model_metadata,
)
from backend.app.specialists.registry import (
    list_specialists,
    predict_all,
    predict_with_specialist,
)
from backend.app.specialists.router_benchmark import benchmark_router
from backend.app.specialists.router_evaluation import (
    evaluate_router_regression,
    load_router_regression_examples,
)
from backend.app.specialists.schemas import (
    ErrorClassification,
    IntentPrediction,
    SpecialistDatasetLifecycleRequest,
    SpecialistDatasetRegisterRequest,
    SpecialistModelLifecycleRequest,
    SpecialistPrediction,
    SpecialistRequest,
    SpecialistTrainRequest,
)
from backend.app.specialists.specialist_router import route_specialist_task
from backend.app.specialists.sklearn_predictor import predict_with_sklearn_model
from backend.app.specialists.sklearn_trainer import (
    load_training_examples,
    train_one_specialist_model,
    train_specialist_models,
)
from backend.app.specialists.training_job_store import (
    create_training_job,
    get_training_job,
    list_training_jobs,
    update_training_job,
)
from backend.app.specialists.trace_store import (
    append_specialist_trace,
    get_specialist_trace,
    list_specialist_traces,
    recent_specialist_traces,
)

__all__ = [
    "ErrorClassification",
    "IntentPrediction",
    "SpecialistDatasetLifecycleRequest",
    "SpecialistDatasetRegisterRequest",
    "SpecialistModelLifecycleRequest",
    "SpecialistPrediction",
    "SpecialistRequest",
    "SpecialistTrainRequest",
    "append_specialist_feedback",
    "append_model_audit_event",
    "approve_dataset",
    "archive_dataset",
    "build_model_metadata",
    "build_model_evaluation_report",
    "build_specialist_dashboard",
    "benchmark_router",
    "calculate_accuracy",
    "calculate_confusion_matrix",
    "calculate_label_counts",
    "classify_error",
    "create_training_job",
    "deactivate_model",
    "deactivate_specialist_model",
    "evaluate_examples",
    "evaluate_specialist_dataset",
    "evaluate_quality_gate",
    "evaluate_router_regression",
    "evaluate_promotion_quality_gate",
    "find_specialist_model",
    "get_dataset",
    "get_specialist_trace",
    "get_training_job",
    "lifecycle_artifact_path",
    "list_datasets",
    "load_router_regression_examples",
    "list_specialist_models",
    "list_specialists",
    "list_specialist_traces",
    "list_training_jobs",
    "load_model_audit_events",
    "load_specialist_model",
    "load_jsonl_dataset",
    "load_specialist_eval_dataset",
    "load_training_examples",
    "predict_all",
    "predict_intent",
    "promote_model",
    "promote_specialist_model",
    "predict_with_sklearn_model",
    "predict_with_specialist",
    "recent_specialist_traces",
    "register_dataset",
    "reject_model",
    "reject_specialist_model",
    "rollback_specialist_model",
    "route_specialist_task",
    "save_specialist_model",
    "specialist_artifact_path",
    "summarize_failures",
    "train_one_specialist_model",
    "train_specialist_models",
    "update_training_job",
    "validate_dataset_row",
    "validate_model_metadata",
]
