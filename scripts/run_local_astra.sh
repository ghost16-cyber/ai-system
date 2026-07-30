#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${ASTRA_RUNTIME_DIR:-$ROOT/data/runtime}"
DB_PATH="${AI_SYSTEM_DB_PATH:-$ROOT/data/app/ai_system.db}"
PYTHON_BIN="${ASTRA_PYTHON_BIN:-$ROOT/.venv/bin/python}"
FRONTEND_DIR="$ROOT/frontend"
RUNTIME_ENV_FILE="${ASTRA_RUNTIME_ENV_FILE:-$ROOT/.astra-stage2c-runtime.env}"

if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  set -a
  # This file contains the already-provisioned pinned image/digest settings.
  source "$RUNTIME_ENV_FILE"
  set +a
fi

mkdir -p "$RUNTIME_DIR"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Astra Python runtime is missing at $PYTHON_BIN; this script never installs packages." >&2
  exit 2
fi
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node and npm must already be available; this script never installs packages." >&2
  exit 2
fi
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "Frontend dependencies are not installed; run installation only with explicit approval." >&2
  exit 2
fi

"$PYTHON_BIN" "$ROOT/scripts/astra_project_doctor.py" --database-path "$DB_PATH"

pids=()
stop_all() {
  local pid
  for pid in "${pids[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; fi
  done
  wait "${pids[@]:-}" 2>/dev/null || true
  rm -f "$RUNTIME_DIR"/*.pid
}
trap stop_all EXIT INT TERM

cd "$ROOT"
"$PYTHON_BIN" -m uvicorn backend.app.main:app --host 127.0.0.1 --port "${ASTRA_BACKEND_PORT:-8000}" >"$RUNTIME_DIR/backend.log" 2>&1 &
pids+=("$!")
echo "$!" >"$RUNTIME_DIR/backend.pid"

ASTRA_PROJECT_EXECUTION_BACKEND=docker "$PYTHON_BIN" -m backend.app.project_workers >"$RUNTIME_DIR/worker.log" 2>&1 &
pids+=("$!")
echo "$!" >"$RUNTIME_DIR/worker.pid"

cd "$FRONTEND_DIR"
npm run dev -- --host 127.0.0.1 --port "${ASTRA_FRONTEND_PORT:-5173}" >"$RUNTIME_DIR/frontend.log" 2>&1 &
pids+=("$!")
echo "$!" >"$RUNTIME_DIR/frontend.pid"

echo "Astra started with explicit PID and log files in $RUNTIME_DIR. Press Ctrl-C to stop cleanly."
wait -n "${pids[@]}"
