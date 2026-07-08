#!/usr/bin/env bash
# Validated 149 launcher for one CyberGym v14/GDB experiment group.
#
# Required for --launch:
#   RUN_ROOT TASK_FILE SERVER_PORT BASE_URL LLM_API_KEY CYBERGYM_GRADING_KEY
#
# Example:
#   RUN_ROOT=/data/pxd-team/workspace/jcy/cyber-agent/runs/v14-gdb-v1-luke \
#   TASK_FILE=/data/pxd-team/workspace/jcy/cyber-agent/runs/v14-gdb-v1-luke/v1_luke.txt \
#   SERVER_PORT=6441 \
#   BASE_URL='<API_ENDPOINT_A>' \
#   LLM_API_KEY='<LLM_API_KEY>' \
#   CYBERGYM_GRADING_KEY='<CYBERGYM_GRADING_KEY>' \
#   bash scripts/launch_v14_gdb_149.sh --launch

set -euo pipefail

MODE="${1:---launch}"
WORKSPACE_ROOT="/data/pxd-team/workspace/jcy/cyber-agent"
CYBERGYM_ROOT="/data/pxd-team/workspace-149/zwq/cybergym"
DATA_DIR="${CYBERGYM_ROOT}/cybergym_data/data"
AGENT_ROOT="${WORKSPACE_ROOT}/cybergym_agent"
QITOS_ROOT="${WORKSPACE_ROOT}/qitos"
PYTHON_BIN="/data3t/conda_envs/cybergym/bin/python3"
BINARY_DIR="/data3t/cybergym-bin/cybergym-server-data"
DOCKER_IMAGE="${DOCKER_IMAGE:-cage/claude-code:cyberdebug}"
DOCKER_NETWORK="${DOCKER_NETWORK:-host}"

RUN_ROOT="${RUN_ROOT:-}"
if [[ -z "$RUN_ROOT" ]]; then
  echo "RUN_ROOT is required" >&2
  exit 2
fi
RUNTIME_CONFIG="${RUN_ROOT}/.v14_gdb_launch.env"

# tmux may have been created by another shell and does not reliably inherit
# arbitrary custom environment variables. Child modes therefore source a
# mode-600 run-local config written by --launch.
if [[ "$MODE" != "--launch" && -f "$RUNTIME_CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$RUNTIME_CONFIG"
fi

TASK_FILE="${TASK_FILE:-}"
SERVER_PORT="${SERVER_PORT:-}"
BASE_URL="${BASE_URL:-}"
LLM_API_KEY="${LLM_API_KEY:-}"
CYBERGYM_GRADING_KEY="${CYBERGYM_GRADING_KEY:-}"
MODEL_NAME="${MODEL_NAME:-GLM-5.1}"
DIFFICULTY="${DIFFICULTY:-level1}"
CONCURRENCY="${CONCURRENCY:-4}"
MAX_STEPS="${MAX_STEPS:-1000000}"
MAX_RUNTIME_SECONDS="${MAX_RUNTIME_SECONDS:-14400}"
GROUP_NAME="${GROUP_NAME:-$(basename "$RUN_ROOT")}"
TMUX_SESSION="${TMUX_SESSION:-jcy-${GROUP_NAME}}"
TRACE_PREFIX="${TRACE_PREFIX:-qitos_${GROUP_NAME}_glm-51}"
OUTPUT_JSONL="${OUTPUT_JSONL:-${RUN_ROOT}/cybergym_${GROUP_NAME}.jsonl}"
SERVER_URL="http://127.0.0.1:${SERVER_PORT}"
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

require_value() {
  local name=$1 value=$2
  if [[ -z "$value" ]]; then
    echo "$name is required" >&2
    exit 2
  fi
}

validate_config() {
  require_value TASK_FILE "$TASK_FILE"
  require_value SERVER_PORT "$SERVER_PORT"
  require_value BASE_URL "$BASE_URL"
  require_value LLM_API_KEY "$LLM_API_KEY"
  require_value CYBERGYM_GRADING_KEY "$CYBERGYM_GRADING_KEY"
  [[ "$SERVER_PORT" =~ ^[0-9]+$ ]] || {
    echo "SERVER_PORT must be numeric: $SERVER_PORT" >&2
    exit 2
  }
  test -x "$PYTHON_BIN"
  test -f "$TASK_FILE"
  test -d "$BINARY_DIR"
  test -d "$QITOS_ROOT/qitos"
  test -x "$QITOS_ROOT/scripts/run_cybergym_batch.py"
}

write_runtime_config() {
  mkdir -p "$RUN_ROOT"
  umask 077
  {
    printf 'TASK_FILE=%q\n' "$TASK_FILE"
    printf 'SERVER_PORT=%q\n' "$SERVER_PORT"
    printf 'BASE_URL=%q\n' "$BASE_URL"
    printf 'LLM_API_KEY=%q\n' "$LLM_API_KEY"
    printf 'CYBERGYM_GRADING_KEY=%q\n' "$CYBERGYM_GRADING_KEY"
    printf 'MODEL_NAME=%q\n' "$MODEL_NAME"
    printf 'DIFFICULTY=%q\n' "$DIFFICULTY"
    printf 'CONCURRENCY=%q\n' "$CONCURRENCY"
    printf 'MAX_STEPS=%q\n' "$MAX_STEPS"
    printf 'MAX_RUNTIME_SECONDS=%q\n' "$MAX_RUNTIME_SECONDS"
    printf 'GROUP_NAME=%q\n' "$GROUP_NAME"
    printf 'TMUX_SESSION=%q\n' "$TMUX_SESSION"
    printf 'TRACE_PREFIX=%q\n' "$TRACE_PREFIX"
    printf 'OUTPUT_JSONL=%q\n' "$OUTPUT_JSONL"
  } > "$RUNTIME_CONFIG"
  chmod 600 "$RUNTIME_CONFIG"
}

sync_agent() {
  (
    flock 9
    QITOS_ROOT="$QITOS_ROOT" bash "$AGENT_ROOT/scripts/sync_to_qitos.sh"
  ) 9>"/tmp/qitos_sync.${USER:-pgroup}.lock"
}

cleanup_batch_containers() {
  local ids
  ids="$(
    docker ps -aq \
      --filter "label=qitos.benchmark=cybergym" \
      --filter "label=cybergym.trace_prefix=${TRACE_PREFIX}" \
      2>/dev/null || true
  )"
  if [[ -n "$ids" ]]; then
    docker rm -f $ids >/dev/null 2>&1 || true
  fi
}

run_server() {
  validate_config
  mkdir -p "$RUN_ROOT/server_poc"
  export CYBERGYM_SOURCE_ROOT="$CYBERGYM_ROOT"
  export CYBERGYM_API_KEY="$CYBERGYM_GRADING_KEY"
  export PYTHONPATH="$CYBERGYM_ROOT/src:${PYTHONPATH:-}"
  exec "$PYTHON_BIN" -m cybergym.server \
    --host 127.0.0.1 \
    --port "$SERVER_PORT" \
    --log_dir "$RUN_ROOT/server_poc" \
    --db_path "$RUN_ROOT/server_poc/poc.db" \
    --binary_dir "$BINARY_DIR"
}

run_batch() {
  validate_config
  sync_agent
  mkdir -p "$RUN_ROOT"
  export CYBERGYM_SOURCE_ROOT="$CYBERGYM_ROOT"
  export PYTHONPATH="$WORKSPACE_ROOT:$QITOS_ROOT:$CYBERGYM_ROOT/src:${PYTHONPATH:-}"

  # Grading authentication is deliberately separate from model authentication.
  export CYBERGYM_API_KEY="$CYBERGYM_GRADING_KEY"
  export CYBERGYM_CLAUDE_AUTH_TOKEN="$LLM_API_KEY"
  export OPENAI_API_KEY="$LLM_API_KEY"
  export OPENAI_BASE_URL="$BASE_URL"

  export CYBERGYM_USE_DOCKER_ENV=1
  export CYBERGYM_DOCKER_IMAGE="$DOCKER_IMAGE"
  export CYBERGYM_DOCKER_NETWORK="$DOCKER_NETWORK"
  export CYBERGYM_ENABLE_DYNAMIC_TOOLS=1
  export CYBERGYM_STAGE_VUL_BINARY=1
  export CYBERGYM_BINARY_DIR="$BINARY_DIR"

  local batch_pid=""
  cleanup_and_exit() {
    local status=$?
    if [[ -n "$batch_pid" ]]; then
      kill "$batch_pid" >/dev/null 2>&1 || true
      wait "$batch_pid" >/dev/null 2>&1 || true
    fi
    cleanup_batch_containers
    exit "$status"
  }
  trap cleanup_and_exit EXIT HUP INT TERM

  "$PYTHON_BIN" -u "$QITOS_ROOT/scripts/run_cybergym_batch.py" \
    --data-dir "$DATA_DIR" \
    --out-root "$RUN_ROOT" \
    --server "$SERVER_URL" \
    --difficulty "$DIFFICULTY" \
    --model-name "$MODEL_NAME" \
    --base-url "$BASE_URL" \
    --api-key "$LLM_API_KEY" \
    --task-file "$TASK_FILE" \
    --limit 0 \
    --concurrency "$CONCURRENCY" \
    --max-steps "$MAX_STEPS" \
    --max-runtime-seconds "$MAX_RUNTIME_SECONDS" \
    --trace-prefix "$TRACE_PREFIX" \
    --output-jsonl "$OUTPUT_JSONL" \
    --resume &
  batch_pid=$!

  set +e
  wait "$batch_pid"
  local status=$?
  set -e
  batch_pid=""
  cleanup_batch_containers
  trap - EXIT HUP INT TERM
  exit "$status"
}

launch() {
  validate_config
  if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "tmux session already exists: $TMUX_SESSION" >&2
    exit 1
  fi
  if ss -ltn | grep -q ":${SERVER_PORT} "; then
    echo "server port already in use: $SERVER_PORT" >&2
    exit 1
  fi
  docker image inspect "$DOCKER_IMAGE" >/dev/null
  write_runtime_config

  local child_prefix
  printf -v child_prefix 'RUN_ROOT=%q bash %q' "$RUN_ROOT" "$SCRIPT_PATH"
  tmux new-session -d -s "$TMUX_SESSION" -n server \
    "$child_prefix --server 2>&1 | tee '$RUN_ROOT/server.log'"
  sleep 6
  if ! ss -ltn | grep -q ":${SERVER_PORT} "; then
    echo "grading server failed to listen on $SERVER_PORT" >&2
    tail -80 "$RUN_ROOT/server.log" >&2 || true
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
    exit 1
  fi
  tmux new-window -t "$TMUX_SESSION" -n batch \
    "$child_prefix --run 2>&1 | tee '$RUN_ROOT/run.log'"

  echo "Launched $TMUX_SESSION | port=$SERVER_PORT | tasks=$(wc -l < "$TASK_FILE") | dynamic=1 | staged_binary=1"
}

case "$MODE" in
  --server) run_server ;;
  --run) run_batch ;;
  --sync) sync_agent ;;
  --launch) launch ;;
  *)
    echo "Usage: $0 [--launch|--server|--run|--sync]" >&2
    exit 2
    ;;
esac
