#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/data/pxd-team/workspace-149/zwq/qitos-cybergym}"
AGENT_ROOT="${AGENT_ROOT:-/data/pxd-team/workspace-149/zwq/cybergym_agent-fresh}"
CYBERGYM_ROOT="${CYBERGYM_ROOT:-/data/pxd-team/workspace-149/zwq/cybergym}"
PYTHON_BIN="${PYTHON_BIN:-/data3t/conda_envs/cybergym/bin/python}"

RUN_NAME="${RUN_NAME:-batch100_sampled_conc2_v11_maxtok32k_compact60_t3600_api360_failed}"
RUN_ROOT="${RUN_ROOT:-${ROOT}/runs/cybergym/${RUN_NAME}}"
TASKS_PATH="${TASKS_PATH:-${RUN_ROOT}/tasks.txt}"

TMUX_SESSION="${TMUX_SESSION:-zwq-5}"
TMUX_WINDOW_PREFIX="${TMUX_WINDOW_PREFIX:-cg-maxtok32k-v11}"

CYBERGYM_SERVER_HOST="${CYBERGYM_SERVER_HOST:-127.0.0.1}"
CYBERGYM_SERVER_PORT="${CYBERGYM_SERVER_PORT:-8726}"
SERVER_URL="${SERVER_URL:-http://${CYBERGYM_SERVER_HOST}:${CYBERGYM_SERVER_PORT}}"

MODEL_NAME="${MODEL_NAME:-GLM-5.1}"
DIFFICULTY="${DIFFICULTY:-level1}"
CONCURRENCY="${CONCURRENCY:-2}"
MAX_STEPS="${MAX_STEPS:-1000000}"
MAX_RUNTIME_SECONDS="${MAX_RUNTIME_SECONDS:-3600}"
TRACE_PREFIX="${TRACE_PREFIX:-qitos_cybergym_maxtok32k_compact60_t3600_api360_failed}"
DEFAULT_GLM_TOKENIZER_PATH="${DEFAULT_GLM_TOKENIZER_PATH:-/data/pxd-team/workspace-149/zwq/glm-5.1-fp8-tokenizer}"

DEFAULT_PREV_RUNS=(
  "${ROOT}/runs/cybergym/batch100_sampled_conc4_v7"
  "${ROOT}/runs/cybergym/batch100_sampled_conc4_v8"
  "${ROOT}/runs/cybergym/batch100_sampled_conc2_v10_maxtok32k_compact60_failed"
)

if [[ -n "${PREV_RUNS:-}" ]]; then
  # Space-separated run roots, for example:
  # PREV_RUNS="runs/cybergym/a runs/cybergym/b" ./scripts/run_failed_maxtok32k_tmux.sh
  read -r -a PREV_RUN_ROOTS <<< "${PREV_RUNS}"
else
  PREV_RUN_ROOTS=("${DEFAULT_PREV_RUNS[@]}")
fi

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

load_model_env() {
  if [[ -f /tmp/cg_smoke_env.sh ]]; then
    # shellcheck source=/dev/null
    source /tmp/cg_smoke_env.sh
  fi

  local secrets_file="${SECRETS_FILE:-${ROOT}/runs/cybergym/runtime1h_p6_iter3/run_batch_p6.sh}"
  if [[ (-z "${CYBERGYM_CLAUDE_AUTH_TOKEN:-}" || -z "${CYBERGYM_API_KEY:-}" || -z "${OPENAI_BASE_URL:-}") && -f "${secrets_file}" ]]; then
    local exports
    exports="$("${PYTHON_BIN}" - "${secrets_file}" <<'PY'
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text()
names = ("CYBERGYM_CLAUDE_AUTH_TOKEN", "CYBERGYM_API_KEY", "OPENAI_BASE_URL", "GLM_BASE_URL")
for name in names:
    pattern = rf"(?:export\s+)?{name}=([\"']?)(.*?)\1(?:\n|$)"
    match = re.search(pattern, text)
    if match:
        print(f"export {name}={shlex.quote(match.group(2))}")
PY
)"
    eval "${exports}"
  fi

  export OPENAI_BASE_URL="${OPENAI_BASE_URL:-${GLM_BASE_URL:-https://glm-zwq.openapi-qb-ai.sii.edu.cn/v1}}"
  export CYBERGYM_CLAUDE_AUTH_TOKEN="${CYBERGYM_CLAUDE_AUTH_TOKEN:-${OPENAI_API_KEY:-}}"
  if [[ -z "${QITOS_GLM_TOKENIZER_PATH:-}" && -d "${DEFAULT_GLM_TOKENIZER_PATH}" ]]; then
    export QITOS_GLM_TOKENIZER_PATH="${DEFAULT_GLM_TOKENIZER_PATH}"
  fi

  if [[ -z "${CYBERGYM_CLAUDE_AUTH_TOKEN:-}" ]]; then
    echo "CYBERGYM_CLAUDE_AUTH_TOKEN is required for model calls." >&2
    exit 1
  fi
}

write_task_file() {
  mkdir -p "${RUN_ROOT}"

  if [[ -n "${TASK_IDS:-}" ]]; then
    printf '%s\n' ${TASK_IDS} > "${TASKS_PATH}"
  elif [[ -n "${TASK_FILE:-}" ]]; then
    cp "${TASK_FILE}" "${TASKS_PATH}"
  else
    "${PYTHON_BIN}" - "${TASKS_PATH}" "${PREV_RUN_ROOTS[@]}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

out_path = Path(sys.argv[1])
run_roots = [Path(arg) for arg in sys.argv[2:]]


def nested(mapping: dict, *keys: str):
    cur = mapping
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def task_from_manifest(path: Path, obj: dict) -> str:
    summary = obj.get("summary") or {}
    for value in (
        nested(summary, "task_meta", "task_id"),
        nested(summary, "task_result", "task_id"),
        nested(obj, "experiment_spec", "benchmark_metadata", "task_id"),
    ):
        if value:
            return str(value)
    marker = "_arvo_"
    name = path.parent.name
    if marker in name:
        return "arvo:" + name.split(marker, 1)[1].split("_", 1)[0]
    return ""


def stop_from_manifest(obj: dict) -> str:
    summary = obj.get("summary") or {}
    task_result = summary.get("task_result") if isinstance(summary.get("task_result"), dict) else {}
    if task_result.get("success") is True:
        return "success"
    return str(task_result.get("stop_reason") or summary.get("stop_reason") or obj.get("status") or "unknown")


status_by_task: dict[str, str] = {}
ordered_tasks: list[str] = []

for root in run_roots:
    traces = root / "traces"
    if not traces.exists():
        continue
    for manifest_path in sorted(traces.glob("*/manifest.json")):
        try:
            obj = json.loads(manifest_path.read_text())
        except Exception:
            continue
        task_id = task_from_manifest(manifest_path, obj)
        if not task_id:
            continue
        if task_id not in status_by_task:
            ordered_tasks.append(task_id)
            status_by_task[task_id] = "unknown"
        stop_reason = stop_from_manifest(obj)
        if stop_reason == "success":
            status_by_task[task_id] = "success"
        elif status_by_task[task_id] != "success":
            status_by_task[task_id] = stop_reason

unresolved = [task for task in ordered_tasks if status_by_task.get(task) != "success"]
out_path.write_text("".join(f"{task}\n" for task in unresolved))
print(f"Wrote {len(unresolved)} unresolved tasks to {out_path}")
for task in unresolved:
    print(f"{task} {status_by_task[task]}")
PY
  fi

  local task_count
  task_count="$(grep -cve '^[[:space:]]*$' "${TASKS_PATH}" || true)"
  if [[ "${task_count}" -eq 0 ]]; then
    echo "No tasks to run. ${TASKS_PATH} is empty." >&2
    exit 1
  fi
  log "TASKS=${TASKS_PATH} COUNT=${task_count}"
}

run_server() {
  mkdir -p "${RUN_ROOT}/server_poc"
  export CYBERGYM_SOURCE_ROOT="${CYBERGYM_ROOT}"
  export PYTHONPATH="${CYBERGYM_ROOT}/src:${PYTHONPATH:-}"

  log "Starting CyberGym server on ${CYBERGYM_SERVER_HOST}:${CYBERGYM_SERVER_PORT}"
  exec "${PYTHON_BIN}" -m cybergym.server \
    --host "${CYBERGYM_SERVER_HOST}" \
    --port "${CYBERGYM_SERVER_PORT}" \
    --log_dir "${RUN_ROOT}/server_poc" \
    --db_path "${RUN_ROOT}/server_poc/poc.db"
}

run_batch() {
  load_model_env
  if [[ ! -s "${TASKS_PATH}" ]]; then
    write_task_file
  fi

  export CYBERGYM_SOURCE_ROOT="${CYBERGYM_ROOT}"
  export PYTHONPATH="${ROOT}:${CYBERGYM_ROOT}/src:${PYTHONPATH:-}"

  cd "${ROOT}"
  log "Syncing ${AGENT_ROOT} into QitOS bundled CyberGym agent"
  bash "${AGENT_ROOT}/scripts/sync_to_qitos.sh"

  log "Running ${MODEL_NAME} on ${TASKS_PATH} via ${SERVER_URL}"
  exec "${PYTHON_BIN}" -u scripts/run_cybergym_batch.py \
    --data-dir "${CYBERGYM_ROOT}/cybergym_data/data" \
    --out-root "${RUN_ROOT}" \
    --server "${SERVER_URL}" \
    --difficulty "${DIFFICULTY}" \
    --model-name "${MODEL_NAME}" \
    --base-url "${OPENAI_BASE_URL}" \
    --api-key "${CYBERGYM_CLAUDE_AUTH_TOKEN}" \
    --task-file "${TASKS_PATH}" \
    --limit 0 \
    --concurrency "${CONCURRENCY}" \
    --max-steps "${MAX_STEPS}" \
    --max-runtime-seconds "${MAX_RUNTIME_SECONDS}" \
    --trace-prefix "${TRACE_PREFIX}" \
    --resume
}

launch_tmux() {
  write_task_file
  mkdir -p "${RUN_ROOT}"

  if ! tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
    echo "tmux session ${TMUX_SESSION} does not exist." >&2
    exit 1
  fi

  local server_window="${TMUX_WINDOW_PREFIX}-server"
  local run_window="${TMUX_WINDOW_PREFIX}-run"
  if tmux list-windows -t "${TMUX_SESSION}" -F '#W' | grep -qx "${server_window}"; then
    echo "tmux window already exists: ${server_window}" >&2
    exit 1
  fi
  if tmux list-windows -t "${TMUX_SESSION}" -F '#W' | grep -qx "${run_window}"; then
    echo "tmux window already exists: ${run_window}" >&2
    exit 1
  fi

  local env_prefix
  env_prefix="ROOT=${ROOT} AGENT_ROOT=${AGENT_ROOT} CYBERGYM_ROOT=${CYBERGYM_ROOT} PYTHON_BIN=${PYTHON_BIN} RUN_NAME=${RUN_NAME} RUN_ROOT=${RUN_ROOT} TASKS_PATH=${TASKS_PATH} CYBERGYM_SERVER_HOST=${CYBERGYM_SERVER_HOST} CYBERGYM_SERVER_PORT=${CYBERGYM_SERVER_PORT} SERVER_URL=${SERVER_URL} MODEL_NAME=${MODEL_NAME} DIFFICULTY=${DIFFICULTY} CONCURRENCY=${CONCURRENCY} MAX_STEPS=${MAX_STEPS} MAX_RUNTIME_SECONDS=${MAX_RUNTIME_SECONDS} TRACE_PREFIX=${TRACE_PREFIX} DEFAULT_GLM_TOKENIZER_PATH=${DEFAULT_GLM_TOKENIZER_PATH} QITOS_GLM_TOKENIZER_PATH=${QITOS_GLM_TOKENIZER_PATH:-}"

  tmux new-window -t "${TMUX_SESSION}" -n "${server_window}" \
    "cd ${ROOT} && ${env_prefix} bash scripts/run_failed_maxtok32k_tmux.sh --server 2>&1 | tee ${RUN_ROOT}/server.log"
  sleep 5
  tmux new-window -t "${TMUX_SESSION}" -n "${run_window}" \
    "cd ${ROOT} && ${env_prefix} bash scripts/run_failed_maxtok32k_tmux.sh --run 2>&1 | tee ${RUN_ROOT}/run.log"

  log "Launched tmux windows: ${TMUX_SESSION}:${server_window}, ${TMUX_SESSION}:${run_window}"
  log "Run root: ${RUN_ROOT}"
}

case "${1:---launch}" in
  --server)
    run_server
    ;;
  --run)
    run_batch
    ;;
  --prepare)
    write_task_file
    ;;
  --launch)
    launch_tmux
    ;;
  *)
    echo "Usage: $0 [--launch|--prepare|--server|--run]" >&2
    exit 2
    ;;
esac
