# Astra Local Runtime Intelligence

Local Runtime Intelligence is the subsystem that helps Astra understand the
machine it is running on before the SLM chooses tools, models, or execution
settings.

The goal is not just hardware reporting. The goal is to give the SLM practical
machine context:

- what hardware is available;
- what AI/coding tools are installed;
- what the laptop can realistically run;
- what task settings are safe;
- when Astra should prefer RAG, quantization, CPU fallback, or cloud GPU.

## Current Modules

```text
backend/app/local_runtime/
  schemas.py              # structured runtime, capability, and task models
  tool_detector.py        # installed command/package detection
  capability_profile.py   # hardware + tools -> capability labels
  task_optimizer.py       # task text -> safe runtime settings
  runtime_context.py      # full context assembled for API, tools, and SLM
```

The hardware facts come from `backend/app/hardware_ai_optimizer`, which remains
the lower-level probe layer.

## API

```http
GET /runtime/context?task=run%20a%20local%20SLM
```

The response includes:

- `hardware`: CPU, RAM, GPU, VRAM, storage, PyTorch/CUDA facts.
- `tools`: detected commands and Python packages.
- `capabilities`: local ability labels such as `local_slm_inference`,
  `pytorch_cuda_training`, `rag_workflows`, and `large_model_finetuning`.
- `policy`: broad machine rules, including low-VRAM mode and quantized model
  preference.
- `task_optimization`: safe settings for the requested task.
- `slm_context`: compact summary designed for the SLM prompt.

## Orchestrator Integration

Astra now exposes a safe orchestrator action:

```text
get_runtime_context
```

The orchestrator also injects a compact `local_runtime` advisor output at task
start, so the SLM sees machine limits even before it calls the tool directly.

## Low-VRAM Behavior

On 4GB-class GPU machines, Astra should generally:

- prefer quantized local SLMs around 1.5B-3B parameters;
- prefer RAG and tool use over fine-tuning;
- use PyTorch mixed precision when CUDA is available;
- start training with batch size 2-4;
- use gradient accumulation and frozen backbones;
- avoid large LLM fine-tuning and image-generation training locally.

## Phase 11 Validation Contract

Runtime Intelligence now exposes explicit deterministic signals for planner and
SLM use:

- `low_vram_mode`
- `prefer_quantized_models`
- `avoid_large_models`
- `cpu_fallback_allowed`
- `prefer_rag_over_finetuning`
- task-level flags such as `faiss_available`,
  `embedding_workflow_recommended`, `large_model_training_discouraged`,
  `sklearn_available`, and `gpu_required`

The validation tests prove that recommendations change by task and machine
context. For example, a 4GB VRAM profile gets small batches and quantized model
guidance, while a higher-VRAM profile gets a wider PyTorch batch range and does
not discourage larger local training in the same way.

## Next Steps

- Add project/script analyzers that detect batch size, image size, AMP usage,
  model family, and DataLoader settings.
- Add a dry-run batch-size finder for PyTorch workloads.
- Add runtime monitoring for RAM/VRAM during long tasks.
- Teach Astra to propose safe low-VRAM patches for AI training scripts.
- Require future model-training and inference executors to consume an approved
  or downgraded plan result before starting work.

## Phase 12A Planner Gating

`backend/app/local_runtime/planning_rules.py` is the deterministic approval
boundary for proposed AI runtime plans.

```python
result = validate_task_plan(
    task="local_slm",
    requested_plan={
        "strategy": "local_inference",
        "model_size_billion_params": 8,
    },
    runtime_context=context,
)
```

The result is one of:

- `allow`: the requested plan is compatible with the runtime.
- `downgrade`: the requested plan is forbidden, but a safe replacement is
  returned.
- `block`: the requested plan is forbidden and no permitted fallback exists.

Phase 12A enforces:

- no full fine-tuning in low-VRAM mode;
- no oversized local model inference when large models are restricted;
- CPU fallback when CUDA is unavailable and fallback is permitted;
- embedding and retrieval workflows before fine-tuning for RAG tasks;
- CPU-safe classical ML without a GPU dependency.

The gate is available through:

```http
POST /runtime/validate-plan
```

and the orchestrator action:

```text
validate_runtime_plan
```

## Phase 12B Trace Auditing

Runtime gate decisions are now observable in orchestration and benchmark output.

Task state records:

- `validation.runtime_plan`: the latest structured gate result;
- `active_runtime_plan`: the plan Astra is currently permitted to use;
- `runtime_plan_audits`: every allow, downgrade, or block decision and the
  enforcement applied.

Repair trace events include:

- `runtime_plan_decision`
- `runtime_plan_enforced`
- `active_runtime_plan`

The orchestrator enforces the result:

- `block` stops the task immediately;
- `downgrade` activates `recommended_plan`;
- repeated attempts to validate the forbidden plan are rewritten to the active
  downgraded plan;
- `authorize_runtime_plan` fails unless validation happened first and the plan
  matches the active approved plan.

Benchmark reports include:

```json
{
  "runtime_plan_validations": 4,
  "runtime_plan_decision_counts": {
    "allow": 2,
    "downgrade": 1,
    "block": 1
  },
  "runtime_plan_followed_count": 3
}
```

## Phase 13 Execution Profiles

Validated plans are compiled into concrete machine-specific settings by:

```python
build_execution_profile(
    task=task,
    runtime_context=context,
    active_runtime_plan=active_plan,
)
```

Profiles include:

- selected runtime and device;
- required and optional tools;
- concrete task settings;
- safeguards that future executors must enforce;
- the validated source plan.

Supported profiles:

- `local_slm`: model-size limit, quantization requirement, context limit, CPU
  fallback, timeout, and concurrency.
- `pytorch_training`: batch range, gradient accumulation, mixed precision,
  checkpoint interval, memory monitoring, and device selection.
- `rag`: embedding and vector backends, FAISS availability, chunking, `top_k`,
  reranking, and embedding caching.
- `classical_ml`: CPU execution, sklearn pipeline permission, parallel-job cap,
  cross-validation, and joblib persistence.

The enforced orchestration sequence is:

```text
validate_runtime_plan
        ↓
build_execution_profile
        ↓
authorize_runtime_plan
        ↓
future workload executor
```

Any new plan validation invalidates the old execution profile. Authorization
fails if the profile is missing or does not match the active plan.

The API endpoint is:

```http
POST /runtime/execution-profile
```
