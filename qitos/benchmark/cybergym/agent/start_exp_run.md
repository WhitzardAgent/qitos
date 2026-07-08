# Exploration-Only Batch Run Procedure

This document describes how to run exploration-only sink recall evaluations on
the 146 test server. Follow these steps to deploy code changes, launch a batch,
monitor progress, and analyze results.

---

## 1. Prerequisites

- SSH key: `/Users/morinop/Desktop/traj_analyzer/pgroup_rsa`
- Remote user: `pgroup@10.1.2.146`
- Local agent repo: `/Users/morinop/Desktop/traj_analyzer/cybergym_agent`
- Local qitos repo: `/Users/morinop/Desktop/traj_analyzer/qitos` (sibling of agent)

## 2. Deploy Code Changes

### 2a. Push to GitHub

```bash
cd /Users/morinop/Desktop/traj_analyzer/cybergym_agent
git add -A && git commit -m "desc of change"
git push origin para_action
```

If qitos also changed:
```bash
cd /Users/morinop/Desktop/traj_analyzer/qitos
git add -A && git commit -m "desc of change"
git push origin qitos_cybergym
```

### 2b. Pull on Remote

```bash
ssh -i /Users/morinop/Desktop/traj_analyzer/pgroup_rsa pgroup@10.1.2.146

# Agent
cd /data/pxd-team/workspace/jcy/cyber-agent/cybergym_agent
git fetch origin && git checkout para_action && git pull

# QitOS (if changed)
cd /data/pxd-team/workspace/jcy/cyber-agent/qitos
git fetch origin && git checkout qitos_cybergym && git pull

# Sync agent into qitos bundled copy
cd /data/pxd-team/workspace/jcy/cyber-agent/cybergym_agent
QITOS_ROOT=/data/pxd-team/workspace/jcy/cyber-agent/qitos bash scripts/sync_to_qitos.sh
```

If `git pull` fails due to untracked file conflicts:
```bash
# Check what's blocking
git status
# Remove conflicting untracked files (e.g., analysis/worker.py)
rm -f <conflicting_file>
git pull
```

## 3. Configure the Batch Script

Edit `scripts/run_exploration_batch.sh` on the remote:

```bash
# Fields to update per version:
RUN_NAME="MMDD-vN-exp-vague-100"   # e.g., "0702-v10-exp-vague-100"
TASK_FILE_NAME="v7_exp_vague_100.txt"  # task list file (100 arvo IDs)
TMUX_SESSION="jcy-exp-v7"          # tmux session name

# Optional overrides via environment:
MODEL_NAME=GLM-5.1-sii             # LLM model
MAX_STEPS=15                       # max exploration steps
CONCURRENCY=4                      # parallel tasks
```

## 4. Launch the Batch

From the remote server:

```bash
cd /data/pxd-team/workspace/jcy/cyber-agent/cybergym_agent/scripts
bash run_exploration_batch.sh --launch
```

This creates a tmux window in session `jcy-exp-v7` and starts the batch.
The `launch` command also runs `sync_to_qitos.sh` and creates the run directory.

**Important**: `launch()` and `run_batch()` each compute `RUN_ROOT` with
different timestamps. The actual traces go to the `run_batch`-created directory.
Check `ls -td 0702-vN-*` to find the real run directory.

## 5. Monitor Progress

### Quick Check

```bash
ssh -i /Users/morinop/Desktop/traj_analyzer/pgroup_rsa pgroup@10.1.2.146

# Find latest run directory
cd /data/pxd-team/workspace/jcy/cyber-agent/cybergym_agent/scripts
ls -td 0702-vN-* | head -3

# Count completed tasks
RUN_DIR=$(ls -td 0702-vN-* | head -1)
echo "Traces: $(ls ${RUN_DIR}/traces/ | wc -l) / 100"

# Check for errors
grep -l "unrecoverable_error" ${RUN_DIR}/traces/*/run.log | wc -l
grep -l "exit=139" ${RUN_DIR}/batch.log | wc -l

# Attach to tmux for live output
tmux attach -t jcy-exp-v7
# Detach with Ctrl+B then D
```

### Sync Traces to Local

```bash
# From local machine
REMOTE_RUN_DIR="/data/pxd-team/workspace/jcy/cyber-agent/cybergym_agent/scripts/0702-vN-exp-vague-100-MMDD-HHMM"
LOCAL_DIR="/Users/morinop/Desktop/traj_analyzer/cybergym_workspace/exp_vN_traces"

mkdir -p "${LOCAL_DIR}"
rsync -avz --progress \
  -e "ssh -i /Users/morinop/Desktop/traj_analyzer/pgroup_rsa" \
  pgroup@10.1.2.146:"${REMOTE_RUN_DIR}/traces/" \
  "${LOCAL_DIR}/"
```

### Sync run.log Files Only

```bash
LOCAL_DIR="/Users/morinop/Desktop/traj_analyzer/cybergym_workspace/exp_vN_runlogs"
mkdir -p "${LOCAL_DIR}"
rsync -avz --progress \
  --include='*/' --include='*/run.log' --exclude='*' \
  -e "ssh -i /Users/morinop/Desktop/traj_analyzer/pgroup_rsa" \
  pgroup@10.1.2.146:"${REMOTE_RUN_DIR}/traces/" \
  "${LOCAL_DIR}/"
```

## 6. Kill a Running Batch

If a batch needs to be stopped (e.g., due to a bug):

```bash
ssh -i /Users/morinop/Desktop/traj_analyzer/pgroup_rsa pgroup@10.1.2.146

# Find and kill the batch runner processes
tmux attach -t jcy-exp-v7  # Ctrl+C in the batch window
# OR:
pkill -f "run_exploration_batch.sh --run"
# Kill individual runner processes:
pkill -f "_runner.sh"

# Remove bad run directory
rm -rf /data/pxd-team/workspace/jcy/cyber-agent/cybergym_agent/scripts/0702-vN-exp-vague-100-MMDD-HHMM
```

## 7. Analyze Results

### 7a. Sink Recall (on remote or local)

The batch script auto-runs analysis at the end. To manually re-analyze:

```bash
bash run_exploration_batch.sh --analyze <run_dir>
```

Or locally with the analysis script:

```bash
# The inline Python in run_exploration_batch.sh can be extracted.
# Key inputs:
#   - traces/ directory with per-task subdirs
#   - ground_truth_sinks.csv
# Key outputs:
#   - sink_recall_results.csv (per-task)
#   - sink_recall_summary.json (aggregate)
```

### 7b. Sink Recall from Local Traces

```python
# See run_exploration_batch.sh analyze() function for the full Python script.
# It reads .agent/state_*.json from each trace dir, extracts sink_candidates,
# and matches against ground_truth_sinks.csv using exact/suffix/substring matching.
```

### 7c. Empty Step Analysis

To check for steps where the LLM produced no output:

```bash
# Pattern: two STEP banners with no provider=OpenAICompatibleModel between them
# See issues/014 for the V10 timeout bug that caused this.
grep -c "provider=OpenAICompatibleModel" traces/*/run.log
```

## 8. Key Files on Remote

| Path | Purpose |
|------|---------|
| `/data/pxd-team/workspace/jcy/cyber-agent/cybergym_agent` | Agent repo (para_action branch) |
| `/data/pxd-team/workspace/jcy/cyber-agent/qitos` | QitOS framework (qitos_cybergym branch) |
| `/data/pxd-team/workspace-149/zwq/cybergym/cybergym_data/data` | Task data (arvo/ subdirs) |
| `/data/pxd-team/workspace/jcy/cyber-agent/cybergym_agent/scripts/` | Run scripts and output dirs |
| `/data/pxd-team/workspace/jcy/cybergym_agent/scripts/ground_truth_sinks.csv` | Ground truth for recall eval |
| `/data/pxd-team/workspace/jcy/cybergym_agent/scripts/v7_exp_vague_100.txt` | 100 vague task IDs |

## 9. Version History

| Version | Date | Run Name | Notes |
|---------|------|----------|-------|
| v7 | 0701 | 0701-v7-exp-vague-100 | Original run, 41/100 exit=139 (tree-sitter SIGSEGV) |
| v9 | 0702 | 0702-v9-exp-vague-100 | After subprocess isolation, 39% recall, 41/100 exit=139 (cosmetic) |
| v10 | 0702 | 0702-v10-exp-vague-100 | Timeout bug — 39/100 unrecoverable_error (issue #014) |

## 10. Common Pitfalls

1. **Two RUN_ROOT directories**: `launch()` creates one, `run_batch()` creates another.
   Traces always go to the `run_batch` directory. Use `ls -td 0702-*` to find the right one.

2. **Git pull conflicts**: Untracked files (e.g., `analysis/worker.py`, `analysis/_index_worker.py`)
   can block `git pull`. Remove them before pulling.

3. **Sync before launch**: Always run `sync_to_qitos.sh` before launching. The agent
   runs from the qitos bundled copy, not the cybergym_agent repo directly.

4. **Exit code 139 is cosmetic**: tree-sitter SIGSEGV during process exit (GC cleanup)
   produces exit=139 but all agent data is complete. See issue #015.

5. **Per-file timeout must be >= 5s**: The subprocess isolation approach spawns a new
   Python process per file. Startup takes ~0.5-1s. See issue #014.
