#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="/data/pxd-team/workspace-149/zwq/qitos-cybergym/runs/cybergym/batch100_sampled_conc4_v7"
PORT="${CYBERGYM_SERVER_PORT:-8722}"
HOST="${CYBERGYM_SERVER_HOST:-127.0.0.1}"
LOG_DIR="${RUN_DIR}/server_poc"
DB_PATH="${LOG_DIR}/poc.db"
export CYBERGYM_SOURCE_ROOT=/data/pxd-team/workspace-149/zwq/cybergym
export PYTHONPATH=/data/pxd-team/workspace-149/zwq/cybergym/src:${PYTHONPATH:-}

mkdir -p "${LOG_DIR}"

echo "run_dir=${RUN_DIR}"
echo "host=${HOST}"
echo "port=${PORT}"
echo "log_dir=${LOG_DIR}"
echo "db_path=${DB_PATH}"

exec /data3t/conda_envs/cybergym/bin/python -m cybergym.server \
  --host "${HOST}" \
  --port "${PORT}" \
  --log_dir "${LOG_DIR}" \
  --db_path "${DB_PATH}"
