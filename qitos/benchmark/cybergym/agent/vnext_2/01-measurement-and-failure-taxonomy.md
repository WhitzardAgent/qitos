# Task 01 — Measurement & Failure Taxonomy

## 目标

先把 vNext_2 的效果评估固定下来：每次改动必须知道它影响的是 candidate recall、active selection、PoC recipe、还是 feedback replanning。当前本地 v13 trace 证明，单看 crash rate 不足以指导实现。

## 当前证据

来自 [`TRACE_FINDINGS_V13.md`](TRACE_FINDINGS_V13.md)：

- completed: 77，crash: 58，completed crash rate: 75.3%。
- non-success completed: 19，平均 submit 15.32。
- failure buckets:
  - candidate_set_miss: 10
  - budget_after_many_submits: 5
  - condition_mapping_failure: 2
  - active_near_gt_no_trigger: 1
  - submit_not_called: 1

本地 trace 没有完整 `model_response.json`，现有 `scripts/evaluate_trace_sink_hit_rate.py` 无法抽到 candidates；必须支持 `tui.log` 和 `assembled_messages.json` fallback。

## 实施状态（2026-07-04）

Task 01 第一版已落地。它仍然是 **offline-only evaluator**，不会把 `error.txt` / GT stack / failure bucket 注入 runtime agent context。

实际代码落点：

- `scripts/evaluate_trace_sink_hit_rate.py`
  - 新增 `discover_trace_dirs(...)`，支持单 trace、group/trace、以及递归 trace root；
  - 新增 `extract_trace_candidates_from_tui(...)`；
  - 新增 `extract_trace_candidates_from_assembled(...)`；
  - 新增 `trace_action_stats(...)`；
  - 新增 `trace_context_stats(...)`；
  - `extract_trace_candidates(...)` 现在按 `model_response -> tui.log -> assembled_messages` fallback；
  - CSV 增加 first action、submit count、context audit、failure bucket、crash family、path/no-crash 统计字段；
  - summary JSON 增加 `failure_buckets`、`action_stats`、`context_stats`、`no_crash_unknown_rate`、`path_normalization_warning_rate`。
- `offline_eval/sink_failure_taxonomy.py`
  - 新增 `classify_trace_failure(...)`；
  - 第一版分桶：`success`、`running`、`no_candidate_recorded`、`submit_not_called`、`candidate_set_miss`、`condition_mapping_failure`、`budget_after_many_submits`、`active_near_gt_no_trigger`、`gt_in_topk_but_not_active`、`no_crash_unknown`。
- `scripts/compare_run_versions.py`
  - 新增 v12/v13 或 v13/v14 run 对比脚本；
  - 输出 `crash/completed`、`crash/all_started`、Exact/Path/Causal recall、failure bucket、first action、submit count、observation audit；
  - 支持 JSON 和 Markdown 输出。
- Tests
  - 新增 `tests/test_trace_fallback_extraction.py`；
  - 新增 `tests/test_sink_failure_taxonomy.py`；
  - 保持 `tests/test_ground_truth_trace_hit_rate.py` / `tests/test_error_stack_evaluator.py` 兼容。

验证命令：

```text
python3 -m pytest \
  tests/test_trace_fallback_extraction.py \
  tests/test_sink_failure_taxonomy.py \
  tests/test_ground_truth_trace_hit_rate.py \
  tests/test_error_stack_evaluator.py -q

14 passed
```

在当前本地同步后的 `remote_traces_v13` 上运行：

```text
python3 scripts/evaluate_trace_sink_hit_rate.py \
  --ground-truth ground_truth/error_stack_sinks_v1.jsonl \
  --trace-root /Users/morinop/Desktop/traj_analyzer/cybergym_workspace/remote_traces_v13 \
  --results-csv /tmp/v13_sink_eval_task01.csv \
  --summary-json /tmp/v13_sink_eval_task01.json \
  --stdout compact
```

当前本地 trace root 已经不再是早先 `TRACE_FINDINGS_V13.md` 的 99-trace 快照，而是：

```text
trace_count=137
evaluated_traces=137
completed=115
success=87
crash/completed=75.65%
tasks_with_candidates=129
ExactSinkRecall@5=40.15%
CrashPathRecall@5=58.39%
CausalCoverage@5=18.98%
failure_buckets={
  success: 87,
  running: 22,
  candidate_set_miss: 12,
  condition_mapping_failure: 6,
  budget_after_many_submits: 5,
  active_near_gt_no_trigger: 2,
  submit_not_called: 2,
  no_crash_unknown: 1
}
context_count=2151
six_section_count=2075
old_marker_count=0
required_conditions_pending_count=808
path_not_reached_without_evidence_count=1260
path_normalization_warning_count=0
```

解读：

- completed crash rate 仍约 75% 量级，和 v13 设计结论一致；
- `candidate_set_miss + condition_mapping_failure + budget_after_many_submits` 仍是下一步主攻；
- `path_not_reached_without_evidence_count` 很高，进一步支持 Task 00/05 的 no-crash taxonomy 改造；
- 当前 v13 trace 尚未包含 Task 00 的 path normalization warning 渲染，因此 `path_normalization_warning_count=0` 是合理的。

## 具体代码修改

### 1. 扩展 `scripts/evaluate_trace_sink_hit_rate.py`

新增 fallback extraction：

- 从 `tui.log` 抽：
  - `Action(name='record_sink_candidate', args={'function': '...'`
  - `[submit_poc(`
  - `Action(name='analyze_description'`
  - `stop=success`
  - `stop=budget_time`
  - `VUL TRIGGERED`
  - `DONE`
- 从 `assembled_messages.json` 抽：
  - `<RUNTIME_CONTEXT>` count
  - `- Sink: \`...\`` confirmed sink fallback
  - six-section compliance
  - `Pending: no PoC-relevant conditions`
  - `**SUBMIT NOW**`

建议新增函数：

```python
def extract_trace_candidates_from_tui(trace_path: Path, seen: set[str]) -> list[TraceCandidate]
def extract_trace_candidates_from_assembled(trace_path: Path, seen: set[str]) -> list[TraceCandidate]
def trace_action_stats(trace_path: Path) -> dict[str, Any]
def trace_context_stats(trace_path: Path) -> dict[str, Any]
```

扩展 CSV 字段：

```text
first_candidate_action
first_submit_action
submit_count
analyze_description_count
context_count
six_section_count
old_marker_count
required_conditions_pending_count
submit_now_count
failure_bucket
crash_family
```

### 2. 新增 `offline_eval/sink_failure_taxonomy.py`

实现：

```python
def classify_trace_failure(record, gt_row, task_eval, action_stats, context_stats) -> str:
    ...
```

规则第一版：

```text
success
no_candidate_recorded
candidate_set_miss
gt_in_topk_but_not_active
condition_mapping_failure
budget_after_many_submits
active_near_gt_no_trigger
submit_not_called
running
```

保守规则：

- `candidate_set_miss`: 有 candidate，但 `path_rank` 为空。
- `condition_mapping_failure`: `path_rank` 非空，且 Required Conditions pending contexts 占比较高。
- `budget_after_many_submits`: submit_count >= 8 且非成功。
- `submit_not_called`: completed 非成功且 submit_count == 0。

### 3. 新增 `scripts/compare_run_versions.py`

支持：

```bash
python3 scripts/compare_run_versions.py \
  --ground-truth ground_truth/error_stack_sinks_v1.jsonl \
  --left-name v12 --left-trace-root ... \
  --right-name v13 --right-trace-root ... \
  --results-json /tmp/v12_v13_compare.json \
  --results-md /tmp/v12_v13_compare.md
```

必须输出：

- `crash/completed`
- `crash/all_started`
- 同 task intersection
- ExactSinkRecall@1/3/5
- CrashPathRecall@5
- CausalCoverage@5
- failure bucket delta
- first candidate / first submit
- submit count success vs non-success
- observation audit

### 4. 测试

已新增：

- `tests/test_trace_fallback_extraction.py`
- `tests/test_sink_failure_taxonomy.py`
- 保持/验证 `tests/test_ground_truth_trace_hit_rate.py`

fixtures 放小片段，不提交真实 trace。

## Context / prompt 影响

无 runtime prompt 修改。此任务只做 offline evaluator，不把 GT 或 error stack 引入 runtime。

## Definition of Done

- 本地 `remote_traces_v13` 旧快照能复现：
  - completed = 77
  - success = 58
  - non-success completed = 19
  - candidate_set_miss = 10
  - budget_after_many_submits = 5
- 当前同步后的 `remote_traces_v13` 已扩大到 137 evaluated traces；新 evaluator 已能输出同口径指标，具体数字见上方实施状态。
- evaluator 能在没有 `model_response.json` 时从 `tui.log` / `assembled_messages.json` 抽 candidate。
- 生成的 CSV 足以定位下一版改动到底影响哪个 failure bucket。
