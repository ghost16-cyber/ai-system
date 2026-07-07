from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .dashboard import build_specialist_dashboard
from .dataset_registry import (
    approve_dataset,
    archive_dataset,
    get_dataset,
    list_datasets,
    register_dataset,
)
from .evaluation import evaluate_specialist_dataset
from .feedback_logger import append_specialist_feedback
from .model_audit_logger import load_model_audit_events
from .model_evaluation_report import build_model_evaluation_report
from .model_promoter import deactivate_model, promote_model, reject_model
from .model_rollback import rollback_specialist_model
from .model_store import list_specialist_models
from .registry import predict_all, predict_with_specialist
from .router_benchmark import benchmark_router
from .router_evaluation import evaluate_router_regression
from .schemas import (
    ErrorClassification,
    IntentPrediction,
    SpecialistDatasetLifecycleRequest,
    SpecialistDatasetRegisterRequest,
    SpecialistEvaluationRequest,
    SpecialistFeedback,
    SpecialistModelLifecycleRequest,
    SpecialistPrediction,
    SpecialistRequest,
    SpecialistTrainRequest,
)
from .sklearn_trainer import train_specialist_models
from .specialist_router import route_specialist_task
from .trace_store import get_specialist_trace, list_specialist_traces
from .training_job_store import get_training_job, list_training_jobs
from backend.app.rag.context_service import compact_context, rag_search


router = APIRouter(prefix="/specialists", tags=["specialists"])


@router.post("/intent", response_model=IntentPrediction)
def specialist_intent(request: SpecialistRequest) -> IntentPrediction:
    return predict_with_specialist("intent_classifier", request)  # type: ignore[return-value]


@router.post("/error-classify", response_model=ErrorClassification)
def specialist_error_classify(request: SpecialistRequest) -> ErrorClassification:
    return predict_with_specialist("error_classifier", request)  # type: ignore[return-value]


@router.post("/predict", response_model=list[SpecialistPrediction])
def specialist_predict(request: SpecialistRequest) -> list[SpecialistPrediction]:
    if request.specialist:
        try:
            return [predict_with_specialist(request.specialist, request)]
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
    return predict_all(request)


@router.post("/evaluate")
def specialist_evaluate(request: SpecialistEvaluationRequest | None = None) -> dict:
    dataset_path = request.dataset_path if request else None
    return evaluate_specialist_dataset(dataset_path)


@router.post("/feedback")
def specialist_feedback(feedback: SpecialistFeedback) -> dict:
    return append_specialist_feedback(feedback.model_dump())


@router.post("/train")
def specialist_train(request: SpecialistTrainRequest | None = None) -> dict:
    request = request or SpecialistTrainRequest()
    result = train_specialist_models(
        dataset_path=request.dataset_path,
        feedback_path=request.feedback_path,
        model_dir=request.model_dir,
        thresholds=request.thresholds,
        dataset_id=request.dataset_id,
        dataset_registry_path=request.dataset_registry_path,
        training_job_store_path=request.training_job_store_path,
    )
    if result.get("blocked") is True:
        raise HTTPException(status_code=400, detail=result.get("reason", "Training blocked."))
    return result


@router.get("/models")
def specialist_models(model_dir: str | None = None) -> dict:
    result = list_specialist_models(model_dir)
    result["count"] = len(result["models"])
    return result


@router.get("/dashboard")
def specialist_dashboard(
    model_dir: str | None = None,
    dataset_registry_path: str | None = None,
    training_job_store_path: str | None = None,
    audit_path: str | None = None,
    trace_store_path: str | None = None,
) -> dict:
    return build_specialist_dashboard(
        model_dir=model_dir,
        dataset_registry_path=dataset_registry_path,
        training_job_store_path=training_job_store_path,
        audit_path=audit_path,
        trace_store_path=trace_store_path,
    )


@router.post("/models/{model_id}/promote")
def specialist_model_promote(
    model_id: str,
    request: SpecialistModelLifecycleRequest | None = None,
) -> dict:
    request = request or SpecialistModelLifecycleRequest()
    result = promote_model(model_id, request.model_dir)
    if result.get("promoted") is not True:
        if _not_found(result):
            raise HTTPException(status_code=404, detail=result.get("reason", "Model not found."))
        raise HTTPException(status_code=400, detail=result.get("reason", "Promotion failed."))
    return result


@router.post("/models/{model_id}/deactivate")
def specialist_model_deactivate(
    model_id: str,
    request: SpecialistModelLifecycleRequest | None = None,
) -> dict:
    request = request or SpecialistModelLifecycleRequest()
    result = deactivate_model(model_id, request.model_dir)
    if result.get("deactivated") is not True:
        if _not_found(result):
            raise HTTPException(status_code=404, detail=result.get("reason", "Model not found."))
        raise HTTPException(status_code=400, detail=result.get("reason", "Deactivation failed."))
    return result


@router.post("/models/{model_id}/reject")
def specialist_model_reject(
    model_id: str,
    request: SpecialistModelLifecycleRequest | None = None,
) -> dict:
    request = request or SpecialistModelLifecycleRequest()
    result = reject_model(model_id, request.model_dir)
    if result.get("rejected") is not True:
        if _not_found(result):
            raise HTTPException(status_code=404, detail=result.get("reason", "Model not found."))
        raise HTTPException(status_code=400, detail=result.get("reason", "Rejection failed."))
    return result


@router.get("/models/{model_id}/audit")
def specialist_model_audit(
    model_id: str,
    audit_path: str | None = None,
    model_dir: str | None = None,
) -> dict:
    if model_dir is not None and not any(
        model.get("model_id") == model_id for model in list_specialist_models(model_dir)["models"]
    ):
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")
    return load_model_audit_events(model_id=model_id, path=audit_path)


@router.post("/models/{model_id}/rollback")
def specialist_model_rollback(
    model_id: str,
    request: SpecialistModelLifecycleRequest | None = None,
) -> dict:
    request = request or SpecialistModelLifecycleRequest()
    result = rollback_specialist_model(model_id, request.model_dir)
    if result.get("rolled_back") is not True:
        if _not_found(result):
            raise HTTPException(status_code=404, detail=result.get("reason", "Model not found."))
        raise HTTPException(status_code=400, detail=result.get("reason", "Rollback failed."))
    return result


@router.get("/models/{model_id}/report")
def specialist_model_report(model_id: str, model_dir: str | None = None) -> dict:
    report = build_model_evaluation_report(model_id, model_dir)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")
    return report


@router.post("/datasets/register")
def specialist_dataset_register(request: SpecialistDatasetRegisterRequest) -> dict:
    return register_dataset(
        request.dataset_path,
        dataset_id=request.dataset_id,
        registry_path=request.registry_path,
    )


@router.get("/datasets")
def specialist_datasets(registry_path: str | None = None) -> dict:
    result = list_datasets(registry_path)
    result["count"] = len(result["datasets"])
    return result


@router.get("/datasets/{dataset_id}")
def specialist_dataset_get(dataset_id: str, registry_path: str | None = None) -> dict:
    dataset = get_dataset(dataset_id, registry_path)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset not found: {dataset_id}")
    return dataset


@router.post("/datasets/{dataset_id}/approve")
def specialist_dataset_approve(
    dataset_id: str,
    request: SpecialistDatasetLifecycleRequest | None = None,
) -> dict:
    request = request or SpecialistDatasetLifecycleRequest()
    result = approve_dataset(dataset_id, request.registry_path)
    if result.get("approved") is not True:
        if _not_found(result):
            raise HTTPException(status_code=404, detail=result.get("reason", "Dataset not found."))
        raise HTTPException(status_code=400, detail=result.get("reason", "Approval failed."))
    return result


@router.post("/datasets/{dataset_id}/archive")
def specialist_dataset_archive(
    dataset_id: str,
    request: SpecialistDatasetLifecycleRequest | None = None,
) -> dict:
    request = request or SpecialistDatasetLifecycleRequest()
    result = archive_dataset(dataset_id, request.registry_path)
    if result.get("archived") is not True:
        if _not_found(result):
            raise HTTPException(status_code=404, detail=result.get("reason", "Dataset not found."))
        raise HTTPException(status_code=400, detail=result.get("reason", "Archive failed."))
    return result


@router.get("/training-jobs")
def specialist_training_jobs(store_path: str | None = None) -> dict:
    result = list_training_jobs(store_path)
    result["count"] = len(result["jobs"])
    return result


@router.get("/training-jobs/{job_id}")
def specialist_training_job_get(job_id: str, store_path: str | None = None) -> dict:
    job = get_training_job(job_id, store_path)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Training job not found: {job_id}")
    return job


@router.post("/route")
def specialist_route(request: SpecialistRequest) -> dict:
    return route_specialist_task(request)


@router.post("/route-with-context")
def specialist_route_with_context(request: SpecialistRequest) -> dict:
    workspace_root = request.context.get("workspace_root", ".")
    search = rag_search(workspace_root, query=request.text, limit=3)
    enriched_context = {
        **request.context,
        "rag_context": compact_context(search["results"]),
        "rag_sources": [
            {"path": item.get("path"), "source": item.get("source")}
            for item in search["results"]
        ],
    }
    routed = route_specialist_task(
        request.model_copy(update={"context": enriched_context})
    )
    return {
        **routed,
        "context_used": True,
        "context_summary": enriched_context["rag_context"],
        "context_sources": enriched_context["rag_sources"],
        "advisory_only": True,
        "execution_allowed": False,
    }


@router.get("/traces")
def specialist_traces(
    specialist_name: str | None = None,
    task_type: str | None = None,
    fallback_used: bool | None = None,
    decision_source: str | None = None,
    trace_store_path: str | None = None,
) -> dict:
    return list_specialist_traces(
        specialist_name=specialist_name,
        task_type=task_type,
        fallback_used=fallback_used,
        decision_source=decision_source,
        path=trace_store_path,
    )


@router.get("/traces/{trace_id}")
def specialist_trace_get(trace_id: str, trace_store_path: str | None = None) -> dict:
    trace = get_specialist_trace(trace_id, trace_store_path)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")
    return trace


@router.get("/router/evaluation")
def specialist_router_evaluation() -> dict:
    return evaluate_router_regression()


@router.get("/router/benchmark")
def specialist_router_benchmark() -> dict:
    return benchmark_router()


def _not_found(result: dict) -> bool:
    reason = str(result.get("reason", ""))
    return "not found" in reason.lower()
