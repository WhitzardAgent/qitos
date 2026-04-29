#!/usr/bin/env bash
set -euo pipefail

source /tmp/cg_smoke_env.sh
export CYBERGYM_SOURCE_ROOT=/data/pxd-team/workspace-149/zwq/cybergym
export PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym:/data/pxd-team/workspace-149/zwq/cybergym/src

cd /data/pxd-team/workspace-149/zwq/qitos-cybergym
bash /data/pxd-team/workspace-149/zwq/cybergym_agent-fresh/scripts/sync_to_qitos.sh

/data3t/conda_envs/cybergym/bin/python -u scripts/run_cybergym_batch.py \
  --data-dir /data/pxd-team/workspace-149/zwq/cybergym/cybergym_data/data \
  --out-root /data/pxd-team/workspace-149/zwq/qitos-cybergym/runs/cybergym/batch100_sampled_conc4_v4 \
  --server http://127.0.0.1:8713 \
  --difficulty level1 \
  --model-name GLM-5.1 \
  --base-url "${OPENAI_BASE_URL}" \
  --api-key "${CYBERGYM_CLAUDE_AUTH_TOKEN}" \
  --task-file /data/pxd-team/workspace-149/zwq/qitos-cybergym/runs/cybergym/trace100_multiagent_20260421_110342/tasks.txt \
  --limit 100 \
  --concurrency 4 \
  --max-steps 1000000 \
  --max-runtime-seconds 6000 \
  --trace-prefix qitos_cybergym_batch100sampled \
  2>&1 | tee /data/pxd-team/workspace-149/zwq/qitos-cybergym/runs/cybergym/batch100_sampled_conc4_v4/run.log
