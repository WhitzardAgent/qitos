#!/usr/bin/env bash
# Exploration-only batch runner for sink recall evaluation.
# Runs agent through exploration phase only (no verification server needed),
# then analyzes sink candidates against ground-truth.
#
# Usage:
#   bash run_exploration_batch.sh --launch
#   bash run_exploration_batch.sh --run
#   bash run_exploration_batch.sh --analyze <run_dir>
#   bash run_exploration_batch.sh --info
set -euo pipefail

# ==================================================================
# >>> EDIT THESE FIELDS <<<
# ==================================================================
RUN_NAME="0702-v9-exp-vague-100"
TASK_FILE_NAME="v7_exp_vague_100.txt"
TMUX_SESSION="jcy-exp-v7"

# ==================================================================
# Fixed paths
# ==================================================================
WORKSPACE_ROOT="/data/pxd-team/workspace/jcy/cyber-agent"
AGENT_ROOT="${WORKSPACE_ROOT}/cybergym_agent"
QITOS_ROOT="${WORKSPACE_ROOT}/qitos"
CYBERGYM_ROOT="/data/pxd-team/workspace-149/zwq/cybergym"
DATA_DIR="${CYBERGYM_ROOT}/cybergym_data/data"
PYTHON_BIN="/home/pgroup/pxd-team/miniconda3/bin/python"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GROUND_TRUTH_CSV="${SCRIPT_DIR}/ground_truth_sinks.csv"

MODEL_NAME="${MODEL_NAME:-GLM-5.1-sii}"
BASE_URL="${BASE_URL:-https://o89mbdpaameoceb5jogodkgegdj95hpe.openapi-qb-ai.sii.edu.cn/v1}"
SECRET_FILE="/data/pxd-team/workspace-149/zwq/qitos-cybergym/runs/cybergym/runtime1h_p6_iter3/run_batch_p6.sh"
MAX_STEPS="${MAX_STEPS:-15}"
CONCURRENCY="${CONCURRENCY:-4}"

# ==================================================================
# Resolve API key from secret file
# ==================================================================
resolve_api_key() {
  "${PYTHON_BIN}" - "${SECRET_FILE}" <<'PY'
import re, shlex, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
for name in ("CYBERGYM_CLAUDE_AUTH_TOKEN", "CYBERGYM_API_KEY"):
    m = re.search(rf"(?:export\s+)?{name}=([\"']?)(.*?)\1(?:\n|$)", text)
    if m:
        print(m.group(2))
        break
PY
}

# ==================================================================
# Run a single task exploration-only
# Env vars required: RUN_ROOT, DATA_DIR, MODEL_NAME, BASE_URL,
#   MAX_STEPS, WORKSPACE_ROOT, QITOS_ROOT, CYBERGYM_ROOT
# ==================================================================
# run_single is defined inline in run_batch to avoid export -f issues

# ==================================================================
# Run all tasks with concurrency
# ==================================================================
run_batch() {
  # Create a fresh, timestamped run directory for THIS invocation only
  RUN_ROOT="${SCRIPT_DIR}/${RUN_NAME}-$(date +%m%d-%H%M)"
  local TRACE_DIR="${RUN_ROOT}/traces"

  local api_key
  api_key="$(resolve_api_key)"
  if [[ -z "${api_key}" ]]; then
    echo "ERROR: could not resolve API key from ${SECRET_FILE}" >&2
    exit 1
  fi

  mkdir -p "${TRACE_DIR}"
  cp "${SCRIPT_DIR}/${TASK_FILE_NAME}" "${RUN_ROOT}/${TASK_FILE_NAME}"
  cp "${GROUND_TRUTH_CSV}" "${RUN_ROOT}/ground_truth_sinks.csv"

  echo "=== Exploration-only batch ==="
  echo "Run: ${RUN_ROOT}"
  echo "Tasks: ${TASK_FILE_NAME} ($(wc -l < "${SCRIPT_DIR}/${TASK_FILE_NAME}") tasks)"
  echo "Model: ${MODEL_NAME}"
  echo "Max steps: ${MAX_STEPS}"
  echo "Concurrency: ${CONCURRENCY}"
  echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
  echo ""

  # Export all variables needed by run_single (called via xargs subshell)
  export RUN_ROOT DATA_DIR MODEL_NAME BASE_URL MAX_STEPS
  export WORKSPACE_ROOT QITOS_ROOT CYBERGYM_ROOT PYTHON_BIN

  # Write a self-contained runner script so xargs subshells don't need export -f
  # NOTE: heredoc is UNQUOTED so variables expand at write time
  local runner="${RUN_ROOT}/_runner.sh"
  cat > "${runner}" <<RUNNER
#!/usr/bin/env bash
set -euo pipefail
task_id="\$1"
api_key="\$2"
issue_id="\${task_id#arvo_}"
task_trace_dir="${RUN_ROOT}/traces/\${task_id}"
task_data_dir="${DATA_DIR}/arvo/\${issue_id}"

mkdir -p "\${task_trace_dir}"

if [[ ! -d "\${task_data_dir}" ]]; then
  echo "[SKIP] \${task_id}: no data dir at \${task_data_dir}"
  echo "\${task_id},skip,no_data_dir" >> "${RUN_ROOT}/_skipped.csv"
  exit 1
fi

export PYTHONPATH="${WORKSPACE_ROOT}:${QITOS_ROOT}:${CYBERGYM_ROOT}/src:\${PYTHONPATH:-}"
CYBERGYM_EXCHANGE_LOG=1 \
CYBERGYM_SOURCE_ROOT="${CYBERGYM_ROOT}" \
OPENAI_API_KEY="\${api_key}" \
OPENAI_BASE_URL="${BASE_URL}" \
"${PYTHON_BIN}" -u -c "
import sys, os, runpy
sp = [p for p in sys.path if 'site-packages' in p]
other = [p for p in sys.path if 'site-packages' not in p]
sys.path = other + sp + ['${WORKSPACE_ROOT}/qitos', '${WORKSPACE_ROOT}']
sys.argv = [
    'cybergym_agent.run_local',
    '--task-id', 'arvo:\${issue_id}',
    '--data-dir', '${DATA_DIR}',
    '--server', 'http://localhost:0',
    '--model', '${MODEL_NAME}',
    '--api-key', '\${api_key}',
    '--base-url', '${BASE_URL}',
    '--max-steps', '${MAX_STEPS}',
    '--exploration-only',
]
os.environ['CYBERGYM_TASK_TRACE_DIR'] = '\${task_trace_dir}'
runpy.run_module('cybergym_agent.run_local', run_name='__main__')
" > "\${task_trace_dir}/run.log" 2>&1

exit_code=\$?
echo "[DONE] \${task_id} exit=\${exit_code}"
exit \${exit_code}
RUNNER
  chmod +x "${runner}"

  # Use xargs for concurrency; each subshell runs the self-contained runner
  cat "${SCRIPT_DIR}/${TASK_FILE_NAME}" | \
    xargs -P "${CONCURRENCY}" -I {} bash "${runner}" {} "${api_key}"

  echo ""
  echo "=== All tasks completed ==="
  echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "Analyzing results..."

  # Auto-analyze after batch completes
  analyze "${RUN_ROOT}"
}

# ==================================================================
# Analyze results: match sink_candidates against ground-truth
# ==================================================================
analyze() {
  local run_root="${1:-}"
  if [[ -z "${run_root}" ]]; then
    # Find the latest run dir
    run_root="$(ls -td "${SCRIPT_DIR}/${RUN_NAME}-"* 2>/dev/null | head -1)"
    if [[ -z "${run_root}" ]]; then
      echo "ERROR: no run directory found for ${RUN_NAME}" >&2
      exit 1
    fi
  fi

  "${PYTHON_BIN}" - "${run_root}" "${GROUND_TRUTH_CSV}" <<'PYEOF'
import csv, json, os, sys, re
from pathlib import Path
from collections import Counter, defaultdict

run_root = sys.argv[1]
gt_csv = sys.argv[2]
trace_dir = Path(run_root) / "traces"
result_csv = Path(run_root) / "sink_recall_results.csv"
summary_json = Path(run_root) / "sink_recall_summary.json"

# Load ground truth
gt_map = {}  # issue_id -> sink function
with open(gt_csv, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        issue_id = row.get("issue_id", "").strip()
        sink = row.get("top_stack_or_site", "").strip()
        if issue_id and sink:
            gt_map[issue_id] = sink

def match_sink(candidate_func, ground_truth):
    if not candidate_func or not ground_truth:
        return "none"
    if candidate_func == ground_truth:
        return "exact"
    if f"::{candidate_func}" in ground_truth:
        return "suffix"
    if ground_truth.endswith(f".{candidate_func}"):
        return "suffix"
    if candidate_func in ground_truth or ground_truth in candidate_func:
        return "substring"
    return "none"

results = []
for task_dir in sorted(trace_dir.iterdir()):
    if not task_dir.is_dir():
        continue
    task_name = task_dir.name  # arvo_XXXXX
    issue_id = task_name.replace("arvo_", "")
    gt_sink = gt_map.get(issue_id, "")

    # Find state snapshots
    candidates = []
    confidences = []
    steps = 0
    error = ""

    # Look for .agent/ state files
    agent_dir = task_dir / ".agent"
    if agent_dir.exists():
        state_files = sorted(agent_dir.glob("state_*.json"))
        if state_files:
            try:
                with open(state_files[-1], encoding="utf-8") as f:
                    state = json.load(f)
                for c in state.get("sink_candidates", []):
                    if isinstance(c, dict) and c.get("status") != "eliminated":
                        candidates.append(c.get("function", ""))
                        confidences.append(float(c.get("confidence", 0)))
                steps = state.get("current_step", 0)
            except Exception as e:
                error = str(e)[:200]

    # Fallback: parse from run.log
    if not candidates:
        log_file = task_dir / "run.log"
        if log_file.exists():
            try:
                log_text = log_file.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r"`(\w+)`\s+\((?:high|medium|low)\s+conf\)", log_text):
                    candidates.append(m.group(1))
                if not candidates:
                    for m in re.finditer(r"Sink Candidates.*?:\s*(.+)", log_text):
                        for func_m in re.finditer(r"`(\w+)`", m.group(1)):
                            candidates.append(func_m.group(1))
            except Exception as e:
                error = str(e)[:200]

    if not candidates and not error:
        log_file = task_dir / "run.log"
        if log_file.exists():
            log_text = log_file.read_text(encoding="utf-8", errors="replace")[-500:]
            if "error" in log_text.lower():
                error = "agent_error"

    # Find best match
    best_level = "none"
    best_conf = 0.0
    for func, conf in zip(candidates, confidences or [0.0] * len(candidates)):
        level = match_sink(func, gt_sink)
        priority = {"exact": 3, "suffix": 2, "substring": 1, "none": 0}
        if priority.get(level, 0) > priority.get(best_level, 0):
            best_level = level
            best_conf = conf

    results.append({
        "task_id": task_name,
        "issue_id": issue_id,
        "ground_truth_sink": gt_sink,
        "sink_candidates": "|".join(candidates) if candidates else "",
        "n_candidates": len(candidates),
        "match_level": best_level,
        "match_confidence": best_conf,
        "recalled": best_level != "none",
        "steps": steps,
        "error": error,
    })

# Write results CSV
with open(result_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "task_id", "issue_id", "ground_truth_sink", "sink_candidates",
        "n_candidates", "match_level", "match_confidence", "recalled",
        "steps", "error",
    ])
    writer.writeheader()
    for r in results:
        writer.writerow(r)

# Compute summary
total = len(results)
if total == 0:
    print("No results to analyze.")
    sys.exit(0)

overall_recall = sum(r["recalled"] for r in results) / total
exact_recall = sum(r["match_level"] == "exact" for r in results) / total
suffix_recall = sum(r["match_level"] == "suffix" for r in results) / total
substr_recall = sum(r["match_level"] == "substring" for r in results) / total

summary = {
    "run_root": str(run_root),
    "total_tasks": total,
    "overall_recall": overall_recall,
    "exact_match_recall": exact_recall,
    "suffix_match_recall": suffix_recall,
    "substring_match_recall": substr_recall,
    "avg_candidates_per_task": sum(r["n_candidates"] for r in results) / total,
    "avg_steps": sum(r["steps"] for r in results) / total,
    "errors": sum(1 for r in results if r["error"]),
    "skipped": sum(1 for r in results if not r["sink_candidates"] and not r["error"]),
}

with open(summary_json, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)

print(f"\n{'='*50}")
print(f"  Sink Recall Results")
print(f"{'='*50}")
print(f"  Run dir:  {run_root}")
print(f"  Tasks: {total}")
print(f"  Overall recall: {overall_recall:.1%}")
print(f"    Exact match:  {exact_recall:.1%}")
print(f"    Suffix match: {suffix_recall:.1%}")
print(f"    Substr match: {substr_recall:.1%}")
print(f"  Avg candidates: {summary['avg_candidates_per_task']:.1f}")
print(f"  Avg steps:      {summary['avg_steps']:.1f}")
print(f"  Errors:         {summary['errors']}")
print(f"  No candidates:  {summary['skipped']}")
print(f"\n  Results: {result_csv}")
print(f"  Summary: {summary_json}")
PYEOF
}

# ==================================================================
# Launch in tmux — one window per experiment, named by run dir
# ==================================================================
launch() {
  # Create run dir first so we know the exact path
  RUN_ROOT="${SCRIPT_DIR}/${RUN_NAME}-$(date +%m%d-%H%M)"
  mkdir -p "${RUN_ROOT}"
  cp "${SCRIPT_DIR}/${TASK_FILE_NAME}" "${RUN_ROOT}/${TASK_FILE_NAME}"
  cp "${GROUND_TRUTH_CSV}" "${RUN_ROOT}/ground_truth_sinks.csv"

  # Sync agent
  QITOS_ROOT="${QITOS_ROOT}" bash "${AGENT_ROOT}/scripts/sync_to_qitos.sh"

  # Window name from run dir timestamp (unique per launch)
  local win_name="batch-$(basename "${RUN_ROOT}" | sed 's/0701-v7-exp-vague-100-//')"

  # Create session if needed, then add a single named window
  tmux has-session -t "${TMUX_SESSION}" 2>/dev/null || tmux new-session -d -s "${TMUX_SESSION}"
  tmux new-window -t "${TMUX_SESSION}" -n "${win_name}" \
    "bash ${SCRIPT_DIR}/run_exploration_batch.sh --run 2>&1 | tee ${RUN_ROOT}/batch.log"

  echo ""
  echo "============================================"
  echo "  Launched: ${TMUX_SESSION}:${win_name}"
  echo "  Run dir:  ${RUN_ROOT}"
  echo "  Batch log: ${RUN_ROOT}/batch.log"
  echo "  Tasks:    ${TASK_FILE_NAME} ($(wc -l < "${SCRIPT_DIR}/${TASK_FILE_NAME}") tasks)"
  echo "  Model:    ${MODEL_NAME}"
  echo "  Max steps:${MAX_STEPS}"
  echo "  Concurrency: ${CONCURRENCY}"
  echo "  Attach:   tmux attach -t ${TMUX_SESSION}"
  echo "============================================"
}

# ==================================================================
show_info() {
  echo "run_name=${RUN_NAME}"
  echo "task_file=${TASK_FILE_NAME}"
  echo "tmux=${TMUX_SESSION}"
  echo "max_steps=${MAX_STEPS}"
  echo "concurrency=${CONCURRENCY}"
  echo "model=${MODEL_NAME}"
  echo "base_url=${BASE_URL}"
}

# Only execute dispatch when run directly (not sourced via xargs)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
case "${1:---launch}" in
  --run)     run_batch ;;
  --analyze) analyze "${2:-}" ;;
  --launch)  launch ;;
  --info)    show_info ;;
  *)
    echo "Usage: $0 [--launch|--run|--analyze [run_dir]|--info]" >&2
    exit 2
    ;;
esac
fi
