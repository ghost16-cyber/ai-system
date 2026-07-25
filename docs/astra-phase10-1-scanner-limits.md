# Phase 10.1 — Repository Scan Budgets and Manifest Completeness

Astra never authorizes canonical project execution against a repository it
has not fully (or trustworthily) accounted for. The scanner (`backend/app/folders/scanner.py`)
and the execution manifest builder (`backend/app/project_analysis/state_manifest.py`)
both enforce firm, configurable, deterministic budgets rather than reading a
repository unbounded — but the budget must be spent on files that actually
matter, not on generated or local artefacts.

## Two budgets, one policy

| | Scanner (`backend/app/folders/scanner.py`) | Manifest builder (`backend/app/project_analysis/state_manifest.py`) |
|---|---|---|
| Purpose | Interactive "connect folder" preview scan behind the chat `FolderAccessCard` | The execution-authorization manifest canonical project creation is gated on |
| Default file count | 1500 | 5000 |
| Default per-file size | 5 MB | 10 MB |
| Default aggregate size | 150 MB | 200 MB |
| Default depth | 12 | 24 |

Both share the same ignore policy, the same dataset-exemption policy, and the
same typed diagnostics shape — only the numeric budgets differ, because the
manifest builder gates something with real consequences (whether execution
can start at all) and is allowed a larger budget than the quick chat preview.

## Configuration

All eight limits are environment-variable configurable. Unset variables use
the defaults above. Values must be positive integers — invalid or non-positive
values raise `FolderScanConfigError` at process startup (fail closed; there is
no implicit unlimited mode).

Scanner (preview scan):
- `ASTRA_SCAN_MAX_FILES`
- `ASTRA_SCAN_MAX_FILE_SIZE_BYTES`
- `ASTRA_SCAN_MAX_TOTAL_SIZE_BYTES`
- `ASTRA_SCAN_MAX_DEPTH`

Manifest builder (execution authorization):
- `ASTRA_MANIFEST_MAX_FILES`
- `ASTRA_MANIFEST_MAX_FILE_SIZE_BYTES`
- `ASTRA_MANIFEST_MAX_TOTAL_SIZE_BYTES`
- `ASTRA_MANIFEST_MAX_DEPTH`

Recommended local values:
- **Small/medium repositories** (a few hundred to ~1,500 eligible files): defaults are sufficient.
- **Large repositories** (multi-thousand files, e.g. monorepos): raise `ASTRA_MANIFEST_MAX_FILES`
  and, if you also want the interactive preview to show the full picture,
  `ASTRA_SCAN_MAX_FILES`. Raise the matching `*_TOTAL_SIZE_BYTES` variable only if warnings show
  the aggregate byte budget (not the file count) was the limiting factor.

## Exclusion happens before budget accounting

Directory-level exclusion (`is_ignored_directory_name` in `scanner.py`) is applied while
walking the tree, before any per-file classification: excluded directories are never
descended into, so their contents never compete for the file-count or byte budget.

This is a **deterministic classification, not a blanket "hidden directories are
generated" heuristic**. A hidden directory (name starting with `.`) is excluded only if
it is explicitly named as generated/local state; it is never excluded merely for
starting with a dot:

- **Excluded by name** (`IGNORED_DIRS`): `.git`, `.venv`/`venv`/`env`, `node_modules`,
  `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.idea`, `dist`, `build`,
  `coverage`, `.next`, `.cache`, `.qa`, `.work`, `logs`, `htmlcov`, `.tox`, `.nox`,
  `.eggs`, `.turbo`, `.parcel-cache`, `.docusaurus`, `checkpoints`, `.runs`. Each is
  matched by exact directory name wherever it appears in the tree — the same mechanism
  `node_modules`/`dist`/`build` already used — not by a path- or depth-specific rule.
  This is what keeps a directory like `benchmarks/.runs/` (thousands of historical
  validation-run JSON files) from ever being walked, without hardcoding that specific
  path.
- **Explicitly never excluded** (`ALLOWED_HIDDEN_DIRS`): `.github`, `.devcontainer`,
  `.vscode`. These hold meaningful repository configuration — CI workflows, dev
  container setup, editor/workspace settings — not generated state. Excluding them would
  let Astra report a manifest "complete" while quietly omitting CI or dev-environment
  configuration from the files it can see and reason about.
- **Everything else** (an unrecognized hidden directory that is on neither list — a
  tool's own dotfolder, a project-specific convention Astra doesn't know about) is
  **scanned normally**, exactly like any other directory. An unknown dot-directory is
  never silently assumed to be generated; only directories Astra has actually classified
  as generated are skipped.

This policy only ever excludes directories by exact name, never files, and never
anything not explicitly classified — an ordinary `src/`, `backend/`, `tests/` tree, or
an unfamiliar hidden directory, is scanned like any other content.

Individual files are still excluded independently of directory policy: sensitive
names/content markers, blocked binary suffixes (archives, compiled artifacts, models,
databases), Windows `Zone.Identifier` download metadata, and now also common temporary
file suffixes (`.tmp`, `.temp`, `.log`, `.bak`, `.swp`, `.swo`, `.orig`, `.rej`, or a
name ending in `~`).

## Dataset/reference-content exemption

Some content is recognized reference/dataset material rather than project source:
files under a top-level `assignment_inputs/`, `datasets/`, `evidence/`, `astra_corpus/`,
or `benchmarks/` directory, or matching a dataset/evidence suffix (`.csv`, `.tsv`,
`.parquet`, `.jsonl`, `.docx`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`). This content:

- is still scanned and still appears in the manifest when small enough to be useful context,
- never competes with source/config/test files for the file-count scan budget,
- never blocks manifest completeness if excluded for being individually oversized.

The exemption predicate (`is_budget_exempt_dataset_content`) lives once in `scanner.py`
and is reused directly by `state_manifest.py`'s required-entry check — the two policies
cannot drift apart.

## Completeness semantics

A manifest (or preview scan) is marked **incomplete** only when the scan genuinely could
not account for everything that matters:

- the file-count budget was exhausted before all eligible files were considered
  (`file_count_budget_exceeded`) — reported with the exact number of eligible files omitted,
- the aggregate byte budget was exhausted (`total_size_limit`),
- the maximum folder depth was reached (`max_depth_reached`),
- an eligible file could not be safely read (`unreadable_or_outside_root`),
- an **important** (non-exempt) file exceeded the per-file size limit (`file_size_limit`,
  filtered through the same dataset-exemption predicate above).

A manifest is **not** marked incomplete merely because generated, hidden, sensitive,
blocked-type, temporary, or exempt dataset/reference content was excluded — that
exclusion is the scanner working as intended, not a gap.

`IncompleteProjectManifestError` messages translate each raw reason into an actionable
hint naming the specific environment variable to raise, e.g. *"the file-count scan limit
was reached (raise ASTRA_MANIFEST_MAX_FILES and/or ASTRA_SCAN_MAX_FILES)"*.

## Diagnostics

Every scan result carries a typed `diagnostics` object (`total_indexed`, `total_eligible`,
`eligible_omitted`, `exempt_dataset_files`, `ignored_generated`, `ignored_sensitive`,
`ignored_unsupported`, `ignored_temporary`, `oversized`, `unreadable`,
`file_count_budget_exceeded`, `total_size_budget_exceeded`, `max_depth_reached`,
`diagnostic_cap_reached`) alongside the existing `complete` flag and per-item inventory.
The chat `FolderAccessCard` renders this directly — which limit was reached, how many
eligible files were omitted, and how many files were excluded as recognized
generated/dataset content — instead of only a generic truncation warning.

## Bounded by design

Even when the configured file-count budget is exhausted, the scanner keeps walking
(bounded by `max_depth`) so it can report an *exact* omitted-file count rather than a
vague "truncated" flag — this is still metadata-only work (a single `stat()` per file,
no content reads). A hard safety valve (`MAX_SCAN_DIAGNOSTIC_ENTRIES`, 50,000 filesystem
entries, or 20× the configured file budget if larger) bounds this diagnostic pass so a
pathological repository can never make a scan unbounded.
