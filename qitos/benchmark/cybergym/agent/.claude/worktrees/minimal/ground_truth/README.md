# Error-stack sink ground truth

`error_stack_sinks_v1.jsonl` and `error_stack_sinks_v1.csv` are offline labels
derived from CyberGym `error.txt` sanitizer outputs.

Generation command:

```bash
python3 scripts/build_error_stack_ground_truth.py \
  --tasks-json /Users/morinop/Desktop/traj_analyzer/cybergym_full_tasks/tasks.json \
  --error-root /Users/morinop/Desktop/traj_analyzer/cybergym_full_tasks/error_txt_only \
  --jsonl-out ground_truth/error_stack_sinks_v1.jsonl \
  --csv-out ground_truth/error_stack_sinks_v1.csv
```

Labeling rule:

- `crash_site` is the first project frame in the sanitizer primary stack.
- `crash_path` is the ordered project-frame prefix of the primary stack.
- `causal_frames` come from sanitizer auxiliary stacks such as freed,
  allocated, stored, and origin stacks.
- `diagnostics` records missing or non-standard stack data explicitly.

These files are for offline evaluation only.  They must not be copied into
CyberGym task workspaces, agent state, observations, prompts, or runtime memory.

To evaluate a run:

```bash
python3 scripts/evaluate_trace_sink_hit_rate.py \
  --ground-truth ground_truth/error_stack_sinks_v1.jsonl \
  --trace-root /path/to/runs/v13-v1-luke/traces \
  --results-csv /tmp/v13_sink_hit_results.csv \
  --summary-json /tmp/v13_sink_hit_summary.json
```
