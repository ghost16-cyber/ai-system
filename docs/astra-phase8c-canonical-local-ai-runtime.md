# Phase 8C — Canonical Local AI Runtime Layer

Astra's Local AI stack (`backend/app/local_ai/`) now depends on a provider-neutral
runtime contract instead of hardcoding Ollama. Ollama remains the shipped,
active provider; llama.cpp is registered as a second, independently
configurable provider that proves the abstraction is correct. No project
control-plane, API, approval, RAG, configuration-version, or idempotency
behavior changed.

## Architecture

```
Chat / workers / synthesis gateway
        |
        v
LocalAIService (configuration, policy, admission,
                optimistic concurrency, idempotency)
        |
        v
ProviderRegistry (lookup only, no policy)
        |
        +-- ollama-local        (OllamaProviderAdapter)
        +-- llama-cpp-local     (LlamaCppProviderAdapter)
        +-- fake-deterministic  (FakeDeterministicProvider)
```

- **`LocalAIService`** (`service.py`) keeps every authority it already had:
  configuration, model/provider profiles, capability snapshots, GPU admission,
  optimistic concurrency (`config_version`), idempotency, and the scheduler.
  It builds one `ProviderRegistry` at construction time and resolves
  `ModelProfile.provider_id -> registry.get(...) -> canonical provider
  instance` — it contains **no provider HTTP implementation details**.
- **`ProviderRegistry`** (`providers/registry.py`) does lookup only: explicit
  `register()`, `get()`/`get_or_none()`, `list_provider_ids()` (deterministic,
  registration order), `resolve_for_model(profile)`. No plugin discovery, no
  hidden globals, no fallback selection — a missing/unresolvable
  `provider_id` always raises `ProviderNotRegisteredError`.
- **Provider adapters** (`providers/ollama.py`, `providers/llama_cpp.py`,
  `providers/fake.py`) own runtime-specific transport and response
  translation only. They implement the same `inspect()`/`generate()` shape
  `LocalGenerationGateway` already used pre-Phase-8C (see below), plus
  `provider_id`, `capabilities` (explicit capability flags), and
  `probe_capability(configuration)` for capability reporting.

## Provider contract (`providers/base.py`)

```python
class ProviderCapabilityDeclaration:
    generation_supported: bool
    structured_output_supported: bool
    cancellation_supported: bool
    streaming_supported: bool
    model_discovery_supported: bool
    loaded_model_discovery_supported: bool
    gpu_supported: bool
    cpu_supported: bool

class CanonicalProvider(Protocol):
    provider_id: str
    capabilities: ProviderCapabilityDeclaration
    def inspect(self, *, timeout_seconds: int) -> ProviderInspection: ...
    def generate(self, request: ProviderGenerationRequest, *, cancelled=None) -> ProviderGenerationResponse: ...
    def probe_capability(self, configuration: LocalAIConfiguration) -> Capability: ...
```

`inspect`/`generate` are deliberately the exact pre-existing
`LocalModelProviderClient` Protocol (`provider.py`) — every adapter is a
drop-in `provider_client` for `LocalGenerationGateway` with zero adaptation.
Unsupported operations are never faked: a provider that cannot discover
installed models declares `model_discovery_supported=False` and returns an
empty tuple rather than an invented list.

## Why `provider.py` and the old `LocalModelProviderClient` still exist

`backend/app/local_ai/provider.py` (the low-level Ollama HTTP client,
`ProviderClientError`/`ProviderErrorCode`) was **already** a clean,
provider-neutral transport contract before this phase — it is reused, not
replaced. `OllamaProviderAdapter` **wraps** (composes, never subclasses) the
existing `OllamaProviderClient`; this is why `tests/test_astra_phase5b_smoke_script.py`'s
class-level monkeypatch of `OllamaProviderClient.inspect`/`.generate` keeps
working unchanged through the new adapter layer — the adapter only delegates.

## Ollama migration

Nothing about Ollama's runtime behavior changed:

- `OllamaProviderAdapter.inspect()`/`.generate()` delegate to the same
  `OllamaProviderClient`, unmodified — same `/api/tags`, `/api/ps`,
  `/api/generate`, `/api/version` calls, same JSON-schema grammar stripping,
  same error mapping.
- `OllamaProviderAdapter.probe_capability()` reproduces the exact
  `OllamaCapability` shape/semantics `_safe_probe()` already produced
  (`endpoint`, `configured_models`, `installed_models`, `loaded_models`,
  `provider_reachable`, `configured_model_missing`) — this method exists for
  registry-driven probing (a future consolidation point) but `_safe_probe()`
  itself still calls the original `_probe_ollama`/`self._ollama_probe`
  machinery verbatim, unchanged, so `tests/test_local_ai_phase4b.py`'s
  `ollama_probe=` constructor injection and module-level `_probe_ollama`
  monkeypatch keep working exactly as before.
- `LocalGenerationGateway`'s default construction (no `provider_client` or
  `provider_registry` given) still builds one `OllamaProviderClient` exactly
  as before Phase 8C.

## What actually changed for generation dispatch

`LocalGenerationGateway` (`generation.py`) now supports three constructor
modes, in priority order:

1. `provider_client=<fixed client>` — used for every request regardless of
   `request.provider_id` (100% of the pre-Phase-8C test suite uses this mode
   and is unaffected).
2. `provider_registry=<ProviderRegistry>` (new) — resolved per request via
   the new `LocalGenerationRequest.provider_id` field. This is what
   `LocalAIService` now wires by default.
3. Neither given — falls back to the exact pre-Phase-8C default (one
   `OllamaProviderClient`).

`LocalAIService.execute_structured_generation()` resolves the target
`ModelProfile` unconditionally (a cheap, read-only lookup) and passes
`provider_id=profile.provider_id` on the generation request, which the
gateway uses to pick the specific adapter instance to call.
`LocalGenerationResult.provider_identity`/`.endpoint_identity` deliberately
stay `configuration.provider_type`/`.endpoint_identity` in every case,
unchanged from before Phase 8C -- an initial version of this change made
them reflect the specific resolved adapter's registry key instead (e.g.
`"ollama-local"`), but that broke an existing test asserting the API's
`slm_provider` field equals the coarse `"ollama"` value; that field's
established vocabulary is the *kind* of provider, not the registry
resolution key, and `configuration.provider_type` already matches whichever
provider a resolved model is expected to back in normal operation, so this
was reverted rather than treated as an intentional response-shape change.

The "installed model" check for a generation request now checks
`installed_models ∪ loaded_models` (previously `installed_models` alone) —
behavior-identical for Ollama (a loaded model is always installed there) and
required for llama.cpp, which can only ever confirm the currently loaded
model.

## llama.cpp provider

`providers/llama_cpp.py` treats `llama-server` as an independently managed
process: this phase never starts it, stops it, compiles llama.cpp,
downloads a GGUF file, or adds native Python bindings — it is an HTTP
client only, using stdlib `urllib` (matching `OllamaProviderClient`'s own
style, no new dependency).

- **Health / loaded-model discovery**: `GET /v1/models` (OpenAI-compatible).
  llama-server only ever reports the model it currently has loaded — there
  is no separate "installed but not loaded" registry the way Ollama's
  `/api/tags` provides. `LlamaCppProviderAdapter.capabilities.model_discovery_supported`
  is therefore `False`, and `ProviderInspection.installed_models` is always
  `()` — never invented — while `loaded_models` reflects what `/v1/models`
  actually reports.
- **Generation**: `POST /v1/chat/completions` (OpenAI-compatible chat
  completion), with `response_format: {"type": "json_object"}` for
  structured output. **Uncertainty flagged deliberately**: whether a given
  llama-server build also supports strict `response_format: {"type":
  "json_schema", ...}` (grammar-constrained decoding, analogous to Ollama's
  `format: <schema>`) is version-dependent and was not assumed or
  implemented here — only the widely-supported basic JSON-object mode is
  used. If your llama-server build supports strict schema decoding, that
  would be a follow-up, not something this phase silently assumed.
- **Usage normalization**: OpenAI's `usage.prompt_tokens`/`completion_tokens`
  are mapped onto the same canonical `metadata` keys Ollama's adapter
  already produces (`prompt_eval_count`/`eval_count`) — this is the existing
  shared vocabulary `generation.py`'s `_usage()` reads regardless of
  provider; it predates this phase and was not renamed.
- **Finish reason**: neither `ProviderGenerationResponse` nor
  `LocalGenerationResult` has ever had a first-class `finish_reason` field
  (Ollama's own adapter doesn't surface `done_reason` either). "Normalization"
  here means the adapter tolerates any `finish_reason` value while extracting
  message content — it does not invent a new field to carry it.
- **Cancellation**: `capabilities.cancellation_supported = False` — the
  OpenAI-compatible HTTP API this adapter uses has no in-flight-cancellation
  primitive. A caller-side cancellation check is still honored *before* the
  HTTP call is made (same semantics `OllamaProviderClient` already has — see
  Cancellation section below).
- **Capability record**: a new `LlamaCppCapability` (`contracts.py`), added
  to the existing `CapabilityRecord` union alongside `OllamaCapability` —
  not overloaded onto `capability_id="ollama"` (the two runtimes have
  genuinely different discovery semantics). This required no migration:
  `CapabilityRecord` is a Pydantic union validated structurally
  (`extra="forbid"` on every `StrictModel` subtype is what already
  disambiguates all 15 pre-existing capability subtypes; `LlamaCppCapability`
  participates the same way).
- **GGUF metadata**: model format/quantization/runtime-kind belong in
  `ModelProfile.source_metadata` (an existing, already-extensible
  `dict[str, Any]` field) — no migration was needed or added. Example:
  ```json
  {
    "model_format": "gguf",
    "quantization": "Q4_K_M",
    "endpoint": "http://127.0.0.1:8081",
    "runtime_kind": "llama.cpp",
    "no_auto_start": true,
    "no_auto_download": true
  }
  ```

## Capability schema decisions

- Every existing CPU/memory/CUDA/GPU/VRAM/PyTorch/ONNX/TensorRT/training
  capability record is untouched, in the same order, same fields.
- The Ollama capability record's fields, computation, and position in the
  snapshot tuple are byte-identical to before this phase.
- A `LlamaCppCapability` entry is appended **only when
  `ASTRA_LLAMA_CPP_ENABLED=true`** — an operator who never configured
  llama.cpp sees the exact same snapshot shape/length as before Phase 8C
  (verified by `test_capability_refresh_omits_llama_cpp_when_not_configured`).
  This is a deliberate, conservative choice: unlike Ollama (which is always
  probed because it is the shipped default), nothing about llama.cpp is
  probed unless the operator explicitly opted in — matching "no automatic
  start" for infrastructure that may not exist at all in a given deployment.
- Capability refresh (`LocalAIService.refresh_capabilities`/`capability_report`)
  remains read-only with respect to `local_ai_models`/`local_ai_providers`
  `config_version` columns — confirmed by
  `test_capability_refresh_includes_configured_providers_and_never_touches_configuration_version`.

## Configuration examples

Ollama (unchanged, still the default):
```bash
ASTRA_LOCAL_AI_PROVIDER=ollama
ASTRA_OLLAMA_ENDPOINT=http://127.0.0.1:11434
ASTRA_LOCAL_AI_MODEL=qwen2.5-coder:1.5b
```

llama.cpp, registered alongside Ollama (does not replace it unless you also
change `ASTRA_LOCAL_AI_PROVIDER`):
```bash
ASTRA_LLAMA_CPP_ENABLED=true
ASTRA_LLAMA_CPP_ENDPOINT=http://127.0.0.1:8081
ASTRA_LLAMA_CPP_MODEL=qwen2.5-coder-7b-q4_k_m.gguf
```

No model profile pointing at `llama-cpp-local` is created automatically —
`default_model_profiles()` still seeds exactly `configured-local-model`
(bound to `ollama-local`) and `fake-deterministic`, matching the existing,
already-safe bootstrap pattern (`INSERT OR IGNORE`, never force-enables
anything). A llama.cpp-backed model profile is something you'd bind
explicitly (this phase's tests do so directly against the database, as a
worked example) or through a future dedicated "register model profile"
endpoint — none exists today for *any* provider, so this phase didn't
invent one just for llama.cpp.

## Error model

Provider-transport errors reuse the existing `ProviderClientError`/
`ProviderErrorCode` taxonomy (`provider.py`), extended additively:

| Code | Meaning |
|---|---|
| `provider_unreachable` | connection failed / refused |
| `generation_timeout` | request exceeded its timeout |
| `generation_cancelled` | cancelled before or during the call |
| `provider_rejected_request` | HTTP error status from the provider |
| `malformed_provider_response` | response failed structural validation |
| `invalid_provider_request` | request itself was invalid (e.g. schema mismatch) |
| `provider_not_registered` *(new)* | no adapter registered for this `provider_id` |
| `unsupported_provider_operation` *(new)* | the provider declares this operation unsupported |
| `model_not_loaded` *(new)* | provider reachable, but the specific model isn't loaded |

These map onto the parallel, pre-existing `GenerationFailureReason` enum
(`generation_contracts.py`), which gained the same three additions. Existing
API status codes/shapes are unchanged: `set_model_enabled`'s
"model not locally available" failure is now a named
`ModelNotLocallyAvailableError(ValueError)`, but `str(exc)` is still exactly
`"model_not_locally_available"`, so `routes.py`'s
`except ValueError as exc: raise HTTPException(409, {"code": str(exc)})`
produces an identical response body.

Higher layers check membership in `generation.SUPPORTED_GENERATION_PROVIDER_TYPES`
(`{"ollama", "llama_cpp"}`) instead of `== "ollama"` — this is the one
constant that replaced both hardcoded provider-name branches found in
`generation.py`'s preflight check and `model_synthesis/gateway.py`'s
`build_synthesis_gateway_from_environment`.

## Cancellation semantics

- **Caller cancellation** (the scheduler marking a job `CANCELLED`): checked
  before *and* after the provider call in `LocalGenerationGateway.generate()`
  — unchanged, provider-neutral, already worked before this phase.
- **HTTP request cancellation**: neither Ollama's nor llama.cpp's client
  ever aborts an in-flight HTTP call once `urlopen()` has started (stdlib
  `urllib` has no mid-request cancel primitive) — this was already true for
  Ollama and remains true for llama.cpp. `_raise_if_cancelled()` is checked
  immediately before and after the request, not during it.
- **Provider-native cancellation**: neither runtime is asked to support this;
  both adapters declare `cancellation_supported=False`, which is accurate —
  claiming `True` would be a lie neither runtime can back up.
- **Abandoning a result**: if the scheduler lease times out while a request
  is in flight, the eventual response (success or failure) is simply
  discarded by the caller-cancellation check above; the HTTP call itself
  still completes or times out on its own.

## Adding a future provider

1. Add an adapter under `providers/` implementing `CanonicalProvider`
   (`inspect`, `generate`, `provider_id`, `capabilities`,
   `probe_capability`) — compose an existing or new low-level HTTP client,
   never reimplement request/response handling inline.
2. If the runtime needs its own capability fields, add a `Capability`
   subtype in `contracts.py` and join the `CapabilityRecord` union (no
   migration needed — it's a Pydantic union over a JSON snapshot column).
3. Register it in `default_provider_registry()` (`service.py`) and add a
   corresponding entry to `default_provider_profiles()` if you want it
   visible via `GET /runtime/local-ai/providers`.
4. If capability probing should include it, add a small additive branch to
   `_safe_probe()` (see `_llama_cpp_capability_records` for the pattern) —
   gate it behind an explicit "enabled" config flag if the runtime isn't
   always expected to be running, matching llama.cpp's approach.
5. Never auto-create a model profile pointing at the new provider; bind one
   explicitly (test fixture, or a future registration endpoint).

## Manual llama-server setup (uncertainty flagged)

This phase does not start, manage, or benchmark a real `llama-server`
process. To try the llama.cpp provider manually, you need your own llama.cpp
build with its `llama-server` binary and a GGUF model file. The concept
(placeholders, not hard-coded paths):

```bash
llama-server \
  --model /path/to/model.gguf \
  --host 127.0.0.1 \
  --port 8081 \
  --ctx-size 4096 \
  --n-gpu-layers 999
```

**These exact flags were not verified against a specific installed
llama.cpp build in this repository** — llama.cpp's server CLI flags have
changed across versions, and this environment has no llama.cpp installation
to check against. Consult the `llama-server --help` output (or your
build's own documentation) for the flags your specific version actually
supports before running this. What *is* verified by this phase's code and
tests is the HTTP surface consumed afterward: `GET /v1/models` and
`POST /v1/chat/completions`, both part of llama.cpp's documented
OpenAI-compatibility layer.

Once running, set:
```bash
export ASTRA_LLAMA_CPP_ENABLED=true
export ASTRA_LLAMA_CPP_ENDPOINT=http://127.0.0.1:8081
export ASTRA_LLAMA_CPP_MODEL=<the model identity /v1/models reports>
```
and call `POST /runtime/local-ai/capabilities/refresh` — the `llama_cpp`
capability record should then show `provider_reachable: true`.

## Benchmark procedure (not run)

No GPU-intensive benchmark was run as part of this phase, per the explicit
constraint against it. If you want to compare Ollama vs. llama.cpp
throughput/latency once both are running locally, the bounded way to do it
without touching canonical authority: call
`POST /runtime/local-ai/generations` (or the equivalent
`LocalAIService.execute_generation`) against each provider's model profile
with the same prompt and `parameters.maximum_output_tokens`, and compare
`LocalGenerationResult.duration_ms`/`usage`. This is a manual, operator-run
comparison — Astra does not run it automatically.

## Deferred work

- No dedicated "register a new model profile" API exists for any provider;
  a llama.cpp-backed model profile is bound directly in the database today
  (as this phase's tests demonstrate) rather than through an endpoint.
- Strict JSON-schema-constrained decoding for llama.cpp (beyond basic
  `json_object` mode) was not implemented, pending confirmation of which
  llama-server versions support it.
- The frontend Local AI Control Centre does not yet display llama.cpp rows
  distinctly (no backend response contract expanded to require it — see
  Frontend section).

## Frontend

No frontend files changed. `GET /runtime/local-ai/models` and
`/capabilities` response *shapes* are unchanged (only additive, and only
under an explicit opt-in the current deployment doesn't set) — the existing
Local AI Control Centre (Phase 8B) continues to compile and function against
the unchanged Ollama-only response shape it already handles.
