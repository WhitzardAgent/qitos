#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/data/pxd-team/workspace-149/zwq/qitos-cybergym}"

export RUN_NAME="${RUN_NAME:-batch100_sampled_conc2_v20_strategy_memory_full100}"
export RUN_ROOT="${RUN_ROOT:-${ROOT}/runs/cybergym/${RUN_NAME}}"
export TASK_FILE="${TASK_FILE:-${ROOT}/runs/cybergym/trace100_multiagent_20260421_110342/tasks.txt}"
export TASKS_PATH="${TASKS_PATH:-${RUN_ROOT}/tasks.txt}"
export TMUX_SESSION="${TMUX_SESSION:-zwq-5}"
export TMUX_WINDOW_PREFIX="${TMUX_WINDOW_PREFIX:-cg-stratmem-v20}"
export CYBERGYM_SERVER_PORT="${CYBERGYM_SERVER_PORT:-8727}"
export CONCURRENCY="${CONCURRENCY:-2}"
export MAX_RUNTIME_SECONDS="${MAX_RUNTIME_SECONDS:-3600}"
export MAX_STEPS="${MAX_STEPS:-1000000}"
export TRACE_PREFIX="${TRACE_PREFIX:-qitos_cybergym_strategy_memory_full100}"

exec "${ROOT}/scripts/run_failed_maxtok32k_tmux.sh" "${@:-}"
