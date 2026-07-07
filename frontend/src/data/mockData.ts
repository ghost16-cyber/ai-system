import type {
  ExecutionProfile,
  FeatureConnection,
  OrchestratorJob,
  PatchProposal,
  RepositoryNode,
  RunHistoryItem,
  RuntimeEvidence,
  RuntimePlanValidation,
  RuntimeResearchManifest,
  RuntimeContext,
  TestRunResult,
  WorkflowScenario,
  ToolCall,
  TraceEvent,
} from "../types/contracts";

export const featureConnections: FeatureConnection[] = [
  {
    id: "dashboard",
    label: "Dashboard",
    state: "mock",
    detail: "Local product overview using centralized fixtures.",
  },
  {
    id: "workspace",
    label: "Workspace",
    state: "mock",
    detail: "Task submission and orchestration are simulated in-browser.",
  },
  {
    id: "runtime",
    label: "Runtime Intelligence",
    state: "mock",
    detail: "Hardware and policy values are representative fixtures.",
  },
  {
    id: "profiles",
    label: "Execution Profiles",
    state: "mock",
    detail: "Profiles mirror the backend contract but are not fetched.",
  },
  {
    id: "traces",
    label: "Trace Audit",
    state: "mock",
    detail: "Audit events are generated from mock workflow state.",
  },
  {
    id: "repository",
    label: "Repository Explorer",
    state: "mock",
    detail: "The file tree is static and cannot read local files.",
  },
  {
    id: "patches",
    label: "Patch Review",
    state: "disabled",
    detail: "Review layout is available; apply remains unavailable.",
  },
  {
    id: "tests",
    label: "Test Results",
    state: "mock",
    detail: "Test output is simulated and cannot execute commands.",
  },
  {
    id: "settings",
    label: "Settings",
    state: "mock",
    detail: "Preferences persist only for the current browser session.",
  },
  {
    id: "toolchain",
    label: "Toolchain",
    state: "mock",
    detail: "Detection results are fixtures until backend integration.",
  },
];

export const runtimeContext: RuntimeContext = {
  machine: {
    cpu: "Intel i5-12500H",
    logicalCores: 12,
    gpu: "RTX 3050 Laptop",
    cudaAvailable: true,
    vramGb: 4,
    ramGb: 32,
    storageFreeGb: 186,
  },
  policy: {
    lowVramMode: true,
    preferQuantizedModels: true,
    avoidLargeModels: true,
    cpuFallbackAllowed: true,
    preferRagOverFinetuning: true,
  },
};

export const runtimeResearchManifest: RuntimeResearchManifest = {
  manifestVersion: "runtime_research_manifest_v1",
  sourceFolder: "system information",
  hardwareBaseline: {
    gpu: "NVIDIA GeForce RTX 3050 Laptop GPU",
    vramGb: 4,
    cudaAvailable: true,
    pytorchCudaAvailable: true,
    cpuThreads: 12,
  },
  facts: [
    {
      id: "rtx3050_compute_capable",
      label: "RTX 3050 CUDA compute is useful",
      status: "confirmed",
      summary:
        "Repeated tensor workloads can benefit from CUDA when data stays on the GPU.",
      evidence:
        "The PyTorch CUDA benchmark reports showed strong speedups for batched and repeated matrix/neural-network workloads.",
    },
    {
      id: "vram_is_primary_limit",
      label: "4 GB VRAM is the main constraint",
      status: "warning",
      summary:
        "Model size, context size, and batch size need conservative limits on this laptop.",
      evidence:
        "The system reports describe the RTX 3050 Laptop GPU as compute-capable but VRAM-limited.",
    },
    {
      id: "fp16_inference_preferred",
      label: "FP16 inference is preferred",
      status: "recommended",
      summary:
        "Inference should prefer CUDA, FP16, and inference mode when numerically acceptable.",
      evidence:
        "Runtime status reports recommend CUDA + FP16 for inference and show low VRAM use in inference runs.",
    },
    {
      id: "training_requires_small_batches",
      label: "Training needs small batches",
      status: "recommended",
      summary:
        "Training should use small batches, gradient accumulation, checkpoints, and VRAM monitoring.",
      evidence:
        "Execution analysis found training much slower and more memory-demanding than inference.",
    },
    {
      id: "rag_over_finetuning",
      label: "Prefer RAG before fine-tuning",
      status: "recommended",
      summary:
        "Knowledge adaptation should start with retrieval and compact context before fine-tuning.",
      evidence:
        "Runtime planning reports recommend RAG, tools, and compact context over large local fine-tuning jobs.",
    },
    {
      id: "full_finetuning_low_vram_unsafe",
      label: "Full fine-tuning is unsafe on low VRAM",
      status: "warning",
      summary:
        "Full fine-tuning of large local models should be blocked or downgraded on this runtime.",
      evidence:
        "The continuation report describes profile gates, VRAM pressure gates, and adaptive fallback.",
    },
  ],
  policyDefaults: {
    lowVramMode: true,
    preferQuantizedModels: true,
    avoidLargeModels: true,
    preferRagOverFinetuning: true,
    cpuFallbackAllowed: true,
    maxRecommendedLocalModelBillionParams: 3,
  },
  usageNote:
    "Research evidence explains Astra decisions. Live hardware and tool probing remains the source of truth for runtime policy.",
};

export const runtimeEvidence: RuntimeEvidence[] = [
  {
    id: "evidence-vram",
    label: "VRAM ceiling",
    value: "4 GB",
    detail: "Low-VRAM mode is active for local model and training decisions.",
    factIds: ["vram_is_primary_limit"],
  },
  {
    id: "evidence-cuda",
    label: "CUDA path",
    value: "Available",
    detail: "CUDA is useful for repeated tensor work when data stays on GPU.",
    factIds: ["rtx3050_compute_capable"],
  },
  {
    id: "evidence-inference",
    label: "Inference precision",
    value: "FP16",
    detail: "Inference favors FP16 and inference mode to reduce memory pressure.",
    factIds: ["fp16_inference_preferred", "vram_is_primary_limit"],
  },
  {
    id: "evidence-training",
    label: "Training profile",
    value: "Small batch",
    detail: "Training is heavier than inference and needs gradual batch policy.",
    factIds: ["training_requires_small_batches", "vram_is_primary_limit"],
  },
  {
    id: "evidence-rag",
    label: "Adaptation path",
    value: "RAG first",
    detail: "Retrieval is preferred before fine-tuning for local knowledge work.",
    factIds: ["rag_over_finetuning", "full_finetuning_low_vram_unsafe"],
  },
];

const allowValidation = (
  reason: string,
  requestedPlan: Record<string, unknown>,
): RuntimePlanValidation => ({
  decision: "allow",
  allowed: true,
  reason,
  blockedSignals: [],
  requestedPlan,
  recommendedPlan: requestedPlan,
});

const downgradeValidation = (
  reason: string,
  blockedSignals: string[],
  requestedPlan: Record<string, unknown>,
  recommendedPlan: Record<string, unknown>,
): RuntimePlanValidation => ({
  decision: "downgrade",
  allowed: false,
  reason,
  blockedSignals,
  requestedPlan,
  recommendedPlan,
});

const blockValidation = (
  reason: string,
  blockedSignals: string[],
  requestedPlan: Record<string, unknown>,
): RuntimePlanValidation => ({
  decision: "block",
  allowed: false,
  reason,
  blockedSignals,
  requestedPlan,
  recommendedPlan: {},
});

const slmSignal = (proposedAction: string, reason: string) => ({
  model: "qwen2.5-coder:1.5b",
  role: "coordinator" as const,
  proposedAction,
  reason,
  advisoryOnly: true,
});

const specialistSignal = (
  specialist: string,
  label: string,
  confidence: number,
  reason: string,
) => ({
  specialist,
  label,
  confidence,
  reason,
  advisoryOnly: true,
});

export const executionProfiles: ExecutionProfile[] = [
  {
    id: "profile-code-repair",
    name: "Safe code repair",
    taskType: "code_repair",
    strategy: "inspect_validate_patch",
    runtime: "Python tools",
    device: "cpu",
    status: "safe",
    settings: [
      { label: "Patch budget", value: "20 changed lines" },
      { label: "Syntax validation", value: "Required" },
      { label: "Test verification", value: "Required" },
      { label: "Rollback", value: "Enabled" },
      { label: "Approval", value: "Review only" },
    ],
    safeguards: [
      "Inspect target files before proposing changes",
      "Reject patches outside the approved file scope",
      "Run tests before any future apply action",
    ],
  },
  {
    id: "profile-local-slm",
    name: "Local SLM",
    taskType: "local_slm",
    strategy: "quantized_inference",
    runtime: "Ollama",
    device: "cuda",
    status: "safe",
    settings: [
      { label: "Model limit", value: "3B parameters" },
      { label: "Quantization", value: "4-bit required" },
      { label: "Context", value: "4,096 tokens" },
      { label: "Parallel jobs", value: "1" },
      { label: "Timeout", value: "120 seconds" },
    ],
    safeguards: [
      "Reject models above the configured size limit",
      "Monitor RAM and VRAM before model load",
      "Allow CPU fallback when CUDA becomes unavailable",
    ],
  },
  {
    id: "profile-pytorch",
    name: "PyTorch training",
    taskType: "pytorch_training",
    strategy: "small_model_training",
    runtime: "PyTorch",
    device: "cuda",
    status: "limited",
    settings: [
      { label: "Batch size", value: "2-4" },
      { label: "Gradient accumulation", value: "4 steps" },
      { label: "Mixed precision", value: "Enabled" },
      { label: "Checkpoint interval", value: "250 steps" },
      { label: "Memory monitor", value: "Required" },
    ],
    safeguards: [
      "Run a one-batch dry run",
      "Reduce batch size after CUDA out-of-memory",
      "Save checkpoints before long intervals",
    ],
  },
  {
    id: "profile-rag",
    name: "Repository RAG",
    taskType: "rag",
    strategy: "embedding_retrieval",
    runtime: "FAISS + embeddings",
    device: "hybrid",
    status: "safe",
    settings: [
      { label: "Embedding backend", value: "sentence-transformers" },
      { label: "Chunk size", value: "512 tokens" },
      { label: "Overlap", value: "64 tokens" },
      { label: "Top K", value: "5" },
      { label: "Reranking", value: "Disabled" },
    ],
    safeguards: [
      "Cache embeddings on disk",
      "Limit retrieved context before prompting",
      "Use a simple index if FAISS is unavailable",
    ],
  },
  {
    id: "profile-classical-ml",
    name: "Classical ML",
    taskType: "classical_ml",
    strategy: "sklearn_pipeline",
    runtime: "scikit-learn",
    device: "cpu",
    status: "safe",
    settings: [
      { label: "Pipeline", value: "Enabled" },
      { label: "GPU dependency", value: "None" },
      { label: "Parallel jobs", value: "4" },
      { label: "Persistence", value: "joblib allowed" },
      { label: "Validation", value: "5-fold CV" },
    ],
    safeguards: [
      "Fit preprocessing inside the pipeline",
      "Persist only validated estimators",
      "Keep CPU jobs capped for laptop thermals",
    ],
  },
];

export const workflowScenarios: WorkflowScenario[] = [
  {
    id: "scenario-code-repair",
    taskKind: "Code repair",
    title: "Safe code repair",
    recommendedPrompt:
      "Inspect this project, run the tests, and repair the smallest safe issue.",
    requestedPlan: {
      strategy: "inspect_validate_patch",
      requires_gpu: false,
      allow_edits: false,
    },
    validation: allowValidation(
      "Code repair is CPU-safe and does not require local model execution.",
      {
        strategy: "inspect_validate_patch",
        requires_gpu: false,
        allow_edits: false,
      },
    ),
    slmSignal: slmSignal(
      "inspect_validate_patch",
      "Understood the request as code repair and proposed a review-only patch workflow.",
    ),
    specialistSignals: [
      specialistSignal(
        "intent_classifier",
        "code_repair",
        0.91,
        "Matched fix, test, and patch workflow language.",
      ),
      specialistSignal(
        "error_classifier",
        "pytest_failure",
        0.74,
        "Prepared to classify test output if the mock workflow runs tests.",
      ),
    ],
    activeProfileId: "profile-code-repair",
    runtimeEvidence: [runtimeEvidence[0], runtimeEvidence[1]],
    policyExplanations: [
      {
        id: "repair-cpu-safe",
        title: "No GPU dependency",
        detail: "Static analysis, patch review, and tests can remain CPU-safe.",
        tone: "green",
        factIds: ["vram_is_primary_limit"],
      },
      {
        id: "repair-disabled-apply",
        title: "Patch apply disabled",
        detail: "The frontend can review a patch, but cannot mutate files.",
        tone: "blue",
        factIds: ["vram_is_primary_limit"],
      },
    ],
    traceDetails: {
      runtime: "Runtime context checked: RTX 3050 / 4 GB VRAM / CPU-safe repair path.",
      research: "Research applied: code repair does not need GPU memory pressure.",
      gate: "Plan allowed because no model load, training, or patch apply is requested.",
      profile: "Execution profile built: Python tools / CPU / review-only patch flow.",
      authorization: "Authorization remains mock-only; dangerous actions are disabled.",
      tools: "Mock analysis and tests prepare a patch proposal for review.",
      response: "A low-risk mock patch is ready for review and all simulated tests pass.",
    },
    patchVisible: true,
    testsVisible: true,
    finalMessage:
      "A low-risk mock patch is ready for review and all simulated tests pass.",
  },
  {
    id: "scenario-local-slm",
    taskKind: "Local SLM",
    title: "Quantized local SLM",
    recommendedPrompt:
      "Set up the best local coding model for this laptop and keep it responsive while I work.",
    requestedPlan: {
      strategy: "local_inference",
      model_size_billion_params: 8,
      requires_gpu: true,
    },
    validation: downgradeValidation(
      "The requested local model exceeds the low-VRAM runtime policy.",
      ["large_local_model"],
      {
        strategy: "local_inference",
        model_size_billion_params: 8,
        requires_gpu: true,
      },
      {
        strategy: "quantized_inference",
        model_size_billion_params: 3,
        use_quantized_model: true,
        allow_cpu_fallback: true,
      },
    ),
    slmSignal: slmSignal(
      "validate_runtime_plan",
      "Understood the request as local SLM setup and proposed runtime validation first.",
    ),
    specialistSignals: [
      specialistSignal(
        "intent_classifier",
        "runtime_check",
        0.82,
        "Local model setup matched GPU, VRAM, and runtime policy language.",
      ),
    ],
    activeProfileId: "profile-local-slm",
    runtimeEvidence: [runtimeEvidence[0], runtimeEvidence[1], runtimeEvidence[2]],
    policyExplanations: [
      {
        id: "slm-low-vram",
        title: "Large models restricted",
        detail: "4 GB VRAM keeps local inference under a 3B quantized profile.",
        tone: "amber",
        factIds: ["vram_is_primary_limit", "fp16_inference_preferred"],
      },
      {
        id: "slm-fallback",
        title: "CPU fallback allowed",
        detail: "The plan keeps a CPU fallback if CUDA becomes unavailable.",
        tone: "green",
        factIds: ["rtx3050_compute_capable"],
      },
    ],
    traceDetails: {
      runtime: "Runtime context checked: CUDA available, 4 GB VRAM, low-VRAM policy active.",
      research: "Research applied: FP16 and quantization are preferred for local inference.",
      gate: "Plan downgraded from 8B local inference to a 3B quantized profile.",
      profile: "Execution profile built: Ollama / CUDA / Q4 / 4,096 context tokens.",
      authorization: "Authorization checks the downgraded plan before any model load.",
      tools: "Mock model load check validates the selected quantized profile.",
      response:
        "A 3B quantized local model profile is ready after the requested plan was downgraded.",
    },
    patchVisible: false,
    testsVisible: false,
    finalMessage:
      "A 3B quantized local model profile is ready after the requested plan was downgraded.",
  },
  {
    id: "scenario-rag",
    taskKind: "RAG workflow",
    title: "RAG-first repository workflow",
    recommendedPrompt:
      "Create a local retrieval plan for this repository and keep the context compact.",
    requestedPlan: {
      strategy: "fine_tuning",
      requires_gpu: false,
    },
    validation: downgradeValidation(
      "RAG tasks should use embeddings and retrieval before fine-tuning.",
      ["finetuning_first_for_rag"],
      {
        strategy: "fine_tuning",
        requires_gpu: false,
      },
      {
        strategy: "embedding_retrieval",
        embedding_workflow: true,
        use_fine_tuning: false,
      },
    ),
    slmSignal: slmSignal(
      "validate_runtime_plan",
      "Understood the request as repository memory and proposed RAG validation.",
    ),
    specialistSignals: [
      specialistSignal(
        "intent_classifier",
        "rag_search",
        0.94,
        "Matched retrieval, embeddings, repository index, and compact context.",
      ),
    ],
    activeProfileId: "profile-rag",
    runtimeEvidence: [runtimeEvidence[0], runtimeEvidence[4]],
    policyExplanations: [
      {
        id: "rag-first",
        title: "RAG before fine-tuning",
        detail: "Embedding and retrieval are safer for local knowledge adaptation.",
        tone: "blue",
        factIds: ["rag_over_finetuning"],
      },
      {
        id: "rag-context",
        title: "Compact context",
        detail: "Top-k retrieval keeps SLM prompts within laptop-friendly limits.",
        tone: "green",
        factIds: ["vram_is_primary_limit"],
      },
    ],
    traceDetails: {
      runtime: "Runtime context checked: FAISS fixture available, low-VRAM policy active.",
      research: "Research applied: retrieval is preferred before fine-tuning.",
      gate: "Plan downgraded from fine-tuning-first to embedding + retrieval.",
      profile: "Execution profile built: sentence-transformers / FAISS / top-k 5.",
      authorization: "Authorization confirms the active plan has fine-tuning disabled.",
      tools: "Mock repository indexing embeds chunks into a local vector index.",
      response:
        "The mock RAG workflow is ready with FAISS, cached embeddings, and top-k 5.",
    },
    patchVisible: false,
    testsVisible: false,
    finalMessage:
      "The mock RAG workflow is ready with FAISS, cached embeddings, and top-k 5.",
  },
  {
    id: "scenario-training-small",
    taskKind: "Model training",
    title: "Small PyTorch training profile",
    recommendedPrompt:
      "Choose safe PyTorch settings for a small image classifier on this laptop.",
    requestedPlan: {
      strategy: "small_model_training",
      requires_gpu: true,
      device: "cuda",
    },
    validation: downgradeValidation(
      "Large training settings were narrowed to a small low-VRAM PyTorch profile.",
      ["low_vram_training"],
      {
        strategy: "pytorch_training",
        requested_batch_size: 16,
        requires_gpu: true,
        device: "cuda",
      },
      {
        strategy: "small_model_training",
        batch_size_range: [2, 4],
        gradient_accumulation: true,
        allow_cpu_fallback: true,
      },
    ),
    slmSignal: slmSignal(
      "build_execution_profile",
      "Understood the request as safe PyTorch training settings for local hardware.",
    ),
    specialistSignals: [
      specialistSignal(
        "intent_classifier",
        "pytorch_training",
        0.93,
        "Matched PyTorch, training, batch, and gradient language.",
      ),
    ],
    activeProfileId: "profile-pytorch",
    runtimeEvidence: [runtimeEvidence[0], runtimeEvidence[1], runtimeEvidence[3]],
    policyExplanations: [
      {
        id: "training-small",
        title: "Small batch required",
        detail: "Training starts at batch 2-4 with gradient accumulation.",
        tone: "amber",
        factIds: ["training_requires_small_batches"],
      },
      {
        id: "training-monitor",
        title: "VRAM monitoring",
        detail: "The profile requires a one-batch dry run before longer training.",
        tone: "blue",
        factIds: ["vram_is_primary_limit", "rtx3050_compute_capable"],
      },
    ],
    traceDetails: {
      runtime: "Runtime context checked: CUDA available, 4 GB VRAM, low-VRAM policy active.",
      research: "Research applied: training is heavier than inference and needs small batches.",
      gate: "Plan downgraded to a small PyTorch profile with gradient accumulation.",
      profile: "Execution profile built: PyTorch / batch 2-4 / AMP / gradient accumulation.",
      authorization: "Authorization confirms the active low-VRAM training plan.",
      tools: "Mock one-batch dry run checks VRAM before training settings are accepted.",
      response:
        "The mock training profile is ready with batch size 2-4 and gradient accumulation.",
    },
    patchVisible: false,
    testsVisible: false,
    finalMessage:
      "The mock training profile is ready with batch size 2-4 and gradient accumulation.",
  },
  {
    id: "scenario-training-blocked",
    taskKind: "Model training",
    title: "Blocked full fine-tuning",
    recommendedPrompt:
      "Full fine-tune an 8B local model on this laptop.",
    requestedPlan: {
      strategy: "full_finetuning",
      model_size_billion_params: 8,
      requires_gpu: true,
      device: "cuda",
    },
    validation: blockValidation(
      "Full fine-tuning is not suitable for the low-VRAM runtime.",
      ["full_finetuning", "large_model_training"],
      {
        strategy: "full_finetuning",
        model_size_billion_params: 8,
        requires_gpu: true,
        device: "cuda",
      },
    ),
    slmSignal: slmSignal(
      "validate_runtime_plan",
      "Understood the request as full fine-tuning and proposed runtime validation before any workload.",
    ),
    specialistSignals: [
      specialistSignal(
        "intent_classifier",
        "pytorch_training",
        0.96,
        "Matched fine-tuning and large local model training language.",
      ),
      specialistSignal(
        "runtime_cost_predictor",
        "blocked",
        0.89,
        "4 GB VRAM baseline indicates full fine-tuning is unsafe.",
      ),
    ],
    activeProfileId: null,
    runtimeEvidence: [runtimeEvidence[0], runtimeEvidence[3], runtimeEvidence[4]],
    policyExplanations: [
      {
        id: "training-block",
        title: "Full fine-tuning blocked",
        detail: "Large training is blocked under the low-VRAM policy.",
        tone: "red",
        factIds: ["full_finetuning_low_vram_unsafe"],
      },
      {
        id: "training-alternative",
        title: "Safer alternatives",
        detail:
          "Use RAG first, or a small training profile when the task truly requires learning.",
        tone: "amber",
        factIds: ["training_requires_small_batches", "rag_over_finetuning"],
      },
    ],
    traceDetails: {
      runtime: "Runtime context checked: CUDA available but only 4 GB VRAM.",
      research: "Research applied: full fine-tuning is unsafe on low VRAM.",
      gate: "Plan blocked before profile creation; no workload may execute.",
      profile: "No execution profile is produced for a blocked plan.",
      authorization: "Authorization denied because blocked plans cannot be approved.",
      tools: "No training tools are invoked in the blocked mock path.",
      response:
        "This plan is blocked. Use RAG, quantized inference, or a smaller training profile instead.",
    },
    patchVisible: false,
    testsVisible: false,
    finalMessage:
      "This plan is blocked. Use RAG, quantized inference, or a smaller training profile instead.",
  },
  {
    id: "scenario-classical-ml",
    taskKind: "Classical ML",
    title: "CPU-safe classical ML",
    recommendedPrompt:
      "Build a lightweight scikit-learn baseline for tabular data without using the GPU.",
    requestedPlan: {
      strategy: "sklearn_pipeline",
      requires_gpu: false,
      device: "cpu",
    },
    validation: allowValidation(
      "Classical ML can run as a CPU-safe workflow with no GPU dependency.",
      {
        strategy: "sklearn_pipeline",
        requires_gpu: false,
        device: "cpu",
      },
    ),
    slmSignal: slmSignal(
      "build_execution_profile",
      "Understood the request as a CPU-safe classical ML baseline.",
    ),
    specialistSignals: [
      specialistSignal(
        "intent_classifier",
        "general_chat",
        0.58,
        "No dedicated classical ML specialist exists yet, so deterministic task policy handles this workflow.",
      ),
    ],
    activeProfileId: "profile-classical-ml",
    runtimeEvidence: [runtimeEvidence[0]],
    policyExplanations: [
      {
        id: "classical-cpu",
        title: "CPU workflow allowed",
        detail: "scikit-learn avoids GPU memory pressure entirely.",
        tone: "green",
        factIds: ["vram_is_primary_limit"],
      },
      {
        id: "classical-baseline",
        title: "Good fallback path",
        detail: "Classical ML is a strong baseline when generation is not required.",
        tone: "blue",
        factIds: ["rag_over_finetuning"],
      },
    ],
    traceDetails: {
      runtime: "Runtime context checked: CPU workflow selected, no CUDA dependency.",
      research: "Research applied: avoiding GPU memory pressure is preferred when generation is not required.",
      gate: "Plan allowed because it uses a CPU-safe scikit-learn pipeline.",
      profile: "Execution profile built: scikit-learn / CPU / joblib persistence.",
      authorization: "Authorization confirms there is no GPU workload to approve.",
      tools: "Mock pipeline setup checks preprocessing and validation settings.",
      response:
        "The classical ML profile is ready with CPU execution and no GPU dependency.",
    },
    patchVisible: false,
    testsVisible: false,
    finalMessage:
      "The classical ML profile is ready with CPU execution and no GPU dependency.",
  },
];

export function scenarioForTaskKind(taskKind: WorkflowScenario["taskKind"]) {
  return (
    workflowScenarios.find(
      (scenario) =>
        scenario.taskKind === taskKind && scenario.id !== "scenario-training-blocked",
    ) ??
    workflowScenarios[1]
  );
}

export function scenarioForTask(taskKind: WorkflowScenario["taskKind"], task: string) {
  const lowered = task.toLowerCase();
  if (
    taskKind === "Model training" &&
    (lowered.includes("full fine-tun") ||
      lowered.includes("full finetun") ||
      lowered.includes("train 8b") ||
      lowered.includes("fine-tune an 8b"))
  ) {
    return (
      workflowScenarios.find(
        (scenario) => scenario.id === "scenario-training-blocked",
      ) ?? scenarioForTaskKind(taskKind)
    );
  }
  return scenarioForTaskKind(taskKind);
}

export const traceEvents: TraceEvent[] = [
  {
    id: "trace-1",
    phase: "runtime",
    title: "Runtime context checked",
    detail: "RTX 3050 / 4 GB VRAM / low-VRAM policy active",
    status: "passed",
    elapsed: "0.8s",
  },
  {
    id: "trace-2",
    phase: "planning",
    title: "Requested plan evaluated",
    detail: "8B local inference exceeded the configured 3B limit",
    status: "warning",
    elapsed: "1.2s",
  },
  {
    id: "trace-3",
    phase: "gate",
    title: "Plan downgraded",
    detail: "Recommended quantized 3B inference with CPU fallback",
    status: "warning",
    elapsed: "1.4s",
  },
  {
    id: "trace-4",
    phase: "profile",
    title: "Execution profile compiled",
    detail: "Ollama / CUDA / Q4 / 4,096 context tokens",
    status: "passed",
    elapsed: "1.7s",
  },
  {
    id: "trace-5",
    phase: "authorization",
    title: "Runtime plan authorized",
    detail: "Execution profile matches the active downgraded plan",
    status: "passed",
    elapsed: "2.0s",
  },
];

export const repositoryTree: RepositoryNode[] = [
  {
    name: "backend",
    path: "backend",
    kind: "folder",
    children: [
      {
        name: "app",
        path: "backend/app",
        kind: "folder",
        children: [
          {
            name: "local_runtime",
            path: "backend/app/local_runtime",
            kind: "folder",
            state: "modified",
            children: [
              {
                name: "execution_profiles.py",
                path: "backend/app/local_runtime/execution_profiles.py",
                kind: "python",
                state: "new",
              },
              {
                name: "planning_rules.py",
                path: "backend/app/local_runtime/planning_rules.py",
                kind: "python",
                state: "modified",
              },
            ],
          },
          {
            name: "orchestrator",
            path: "backend/app/orchestrator",
            kind: "folder",
            children: [
              {
                name: "engine.py",
                path: "backend/app/orchestrator/engine.py",
                kind: "python",
                state: "modified",
              },
            ],
          },
        ],
      },
    ],
  },
  {
    name: "frontend",
    path: "frontend",
    kind: "folder",
    state: "new",
    children: [
      {
        name: "src",
        path: "frontend/src",
        kind: "folder",
        children: [
          {
            name: "App.tsx",
            path: "frontend/src/App.tsx",
            kind: "config",
            state: "new",
          },
          {
            name: "styles.css",
            path: "frontend/src/styles.css",
            kind: "config",
            state: "new",
          },
        ],
      },
    ],
  },
  {
    name: "README.md",
    path: "README.md",
    kind: "markdown",
    state: "modified",
  },
];

export const patchProposals: PatchProposal[] = [
  {
    id: "patch-001",
    file: "backend/app/local_runtime/task_optimizer.py",
    title: "Use a low-VRAM training batch range",
    status: "review",
    risk: "low",
    changedLines: 2,
    oldCode: 'batch_size = [8, 32]\nmixed_precision = False',
    newCode: 'batch_size = [2, 4]\nmixed_precision = True',
    checks: [
      "Python syntax valid",
      "Patch scope within 2 lines",
      "Target file inspected",
    ],
  },
  {
    id: "patch-002",
    file: "training/config.yaml",
    title: "Enable gradient accumulation",
    status: "blocked",
    risk: "medium",
    changedLines: 1,
    oldCode: "gradient_accumulation_steps: 1",
    newCode: "gradient_accumulation_steps: 4",
    checks: [
      "File is outside the current approved patch scope",
      "Manual review required",
    ],
  },
];

export const testRun: TestRunResult = {
  id: "tests-001",
  command: "python -m pytest",
  status: "passed",
  passed: 156,
  failed: 0,
  duration: "43.52s",
  suites: [
    {
      name: "Runtime intelligence",
      status: "passed",
      detail: "30 tests passed",
    },
    {
      name: "Orchestrator safety",
      status: "passed",
      detail: "18 tests passed",
    },
    {
      name: "Patch workflow",
      status: "passed",
      detail: "9 tests passed",
    },
    {
      name: "Static analysis",
      status: "passed",
      detail: "13 tests passed",
    },
  ],
};

export const toolCalls: ToolCall[] = [
  {
    id: "tool-python",
    name: "Python",
    state: "mock",
    status: "ready",
    detail: "3.12.3 / execution runtime",
  },
  {
    id: "tool-pytorch",
    name: "PyTorch",
    state: "mock",
    status: "ready",
    detail: "CUDA build detected",
  },
  {
    id: "tool-ollama",
    name: "Ollama",
    state: "mock",
    status: "ready",
    detail: "Local quantized model runner",
  },
  {
    id: "tool-faiss",
    name: "FAISS",
    state: "mock",
    status: "ready",
    detail: "Vector index backend",
  },
  {
    id: "tool-git",
    name: "Git",
    state: "mock",
    status: "ready",
    detail: "Repository and patch safety",
  },
  {
    id: "tool-llama",
    name: "llama.cpp",
    state: "disabled",
    status: "missing",
    detail: "Optional local inference fallback",
  },
];

export const recentRuns: RunHistoryItem[] = [
  {
    id: "run-parser",
    title: "Repair failing parser tests",
    type: "Code repair",
    status: "Passed",
    meta: "7 steps / 42 sec",
    time: "12 min ago",
    accent: "green",
  },
  {
    id: "run-model",
    title: "Prepare local code model",
    type: "Local SLM",
    status: "Downgraded",
    meta: "3B Q4 / CUDA",
    time: "38 min ago",
    accent: "amber",
  },
  {
    id: "run-rag",
    title: "Index project documentation",
    type: "RAG workflow",
    status: "Passed",
    meta: "1,284 chunks / FAISS",
    time: "Yesterday",
    accent: "green",
  },
];

export const orchestratorJobs: OrchestratorJob[] = [
  {
    id: "job-231",
    title: "Prepare local code model",
    taskType: "Local SLM",
    status: "completed",
    decision: "downgrade",
    duration: "2.0 sec",
    updatedAt: "38 min ago",
  },
  {
    id: "job-230",
    title: "Repair failing parser tests",
    taskType: "Code repair",
    status: "completed",
    decision: "allow",
    duration: "42 sec",
    updatedAt: "12 min ago",
  },
  {
    id: "job-229",
    title: "Full fine-tune 8B model",
    taskType: "Model training",
    status: "blocked",
    decision: "block",
    duration: "1.1 sec",
    updatedAt: "Yesterday",
  },
];
