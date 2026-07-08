# Task 05 — Feedback Replanning & Rollout

## 目标

保留 v13 早 submit、多轮反馈的优势，但防止非成功任务在错误 candidate / mapping 上反复提交。核心是把 submit feedback 转成 typed negative evidence 和 replanning，而不是继续鼓励同类 PoC 变体。

## 当前证据

本地 v13：

- non-success completed = 19。
- avg submits = 15.32。
- `budget_after_many_submits` = 5。
- 代表：
  - arvo:19497 submit 49 次。
  - arvo:17986 submit 34 次。
  - arvo:10013 submit 28 次。
  - arvo:14912 submit 28 次。

这说明 v13 的 submit loop 成功提高了 crash rate，但缺少“何时停止当前路线并重规划”的机制。

抽样细读显示具体冲突：

- `arvo:19497`：`Experiments` 显示 49 consecutive NO_TRIGGER，但 `Next Action` 仍是 `SUBMIT NOW` 5 个 ready PoC。
- `arvo:10013`：`format_gate` 被 path_not_reached feedback questioned，但 ready PoC 继续优先提交。
- `arvo:17986`：context 已显示 “Not recommended: submitting more variants”，但系统没有把这条 negative evidence 变成硬约束。
- `arvo:13249`：29 次 submit 后成功，说明不能禁止所有多 submit；要判断是否有新 recipe/new mutation axis。
- 安全专家建议增加通用 PoC 字节/格式 sanity checker；因此 feedback replanning 应区分 carrier sanity fail 和 source path/gate fail，避免 magic/table directory 明显错误时还去怀疑 sink。
- 用户补充指出：`NO_TRIGGER` / `vul_exit_code=0` 当前容易被历史逻辑默认归类为 `path_not_reached`。这不可靠，因为没有 crash 可能是路径未到，也可能是路径到了但 trigger condition / mutation recipe 不满足。因此 Task 05 必须先引入保守 no-crash taxonomy，避免误导 agent 一直检查路径问题。

## 具体代码修改

### 1. 修改 `state.py`

第一版可用 metadata，后续再 dataclass：

```python
state.metadata["negative_evidence"] = [
  {
    "evidence_id": "...",
    "kind": "no_crash_unknown|path_not_reached|path_reached_no_trigger|trigger_condition_not_satisfied|format_error|carrier_sanity_fail|unreachable_path|wrong_crash|repeated_candidate|bad_seed",
    "candidate_id": "...",
    "ranked_path_id": "...",
    "mapping_id": "...",
    "family_id": "...",
    "summary": "...",
    "avoid_next": "...",
    "created_step": 42,
    "ttl": 8,
  }
]
```

新增 helper 可放在 `agent_impl/feedback.py` 或单独文件：

```python
def append_negative_evidence(state, item): ...
def recent_negative_evidence(state, *, limit=3): ...
def evidence_blocks_action(state, candidate_id, mutation_axis): ...
```

### 2. 修改 `agent_impl/feedback.py`

在 submit result 处理后生成 feedback effect：

```python
{
  "outcome": "success|no_crash_unknown|path_not_reached|path_reached_no_trigger|wrong_crash|too_broad|submit_error",
  "likely_failure_layer": "carrier|dispatch|path_gate|trigger_condition|sink_selection|unknown",
  "recommended_revision": "revise_mapping|rotate_candidate|find_paired_endpoint|repair_carrier|submit_ready|cooldown_family",
  "affected_candidate_id": "...",
  "affected_gate_id": "...",
  "affected_mapping_id": "...",
}
```

规则：

- `vul_exit_code=0` 默认：
  - outcome=`no_crash_unknown`
  - likely_failure_layer=`unknown`
  - 不直接 refute path gates。
  - 文案必须说 “No crash observed; this does not prove the path was not reached.”
- `no_crash_unknown` + source-backed carrier/dispatch/path 已确认 + active recipe unchanged：
  - recommended_revision=`revise_mapping`
  - 可升级为 `path_reached_no_trigger` 或 `trigger_condition_not_satisfied`。
- `no_crash_unknown` + explicit parser/carrier/sanity evidence：
  - kind=`format_error` 或 `carrier_sanity_fail`
  - recommended_revision=`repair_carrier`
- 只有明确 evidence 说明目标 path/gate 未达时，才能用 `path_not_reached`。
- `no_crash_unknown` / `path_reached_no_trigger` + same family + unchanged recipe >= 3：
  - append negative evidence kind=`repeated_candidate`
  - recommended_revision=`rotate_candidate` 或 `revise_mapping`
- `no_crash_unknown` + active candidate role=`path_anchor` + no source evidence that path reached：
  - recommended_revision=`find_downstream_endpoint`
- UAF/uninit 缺 pair：
  - recommended_revision=`find_paired_endpoint`
- format/parser failure：
  - kind=`format_error`
  - likely_failure_layer=`carrier`
- pre-submit sanity fail：
  - kind=`carrier_sanity_fail`
  - likely_failure_layer=`carrier`
  - recommended_revision=`repair_carrier`
  - 不计入 sink/path miss，不触发 candidate cooldown。

### 3. 修改 `family_runtime.py`

`apply_family_queue_discipline()` 增加 recipe/candidate aware discipline：

- 连续 3 次 no-progress 且 no recipe revision -> cooldown。
- 有 partial/progress signal -> 不 cooldown。
- family cooldown reason 要写清：
  - `repeated_no_crash_same_recipe`
  - `candidate_set_miss_suspected`
  - `format_carrier_failed`

### 4. 修改 `submit_queue.py`

`SubmitQueuePolicy.accept()` 增加可选上下文：

```python
def accept(self, candidate: CandidateRecord, *, negative_evidence=None) -> tuple[bool, str]:
```

第一版可不改签名，改调用处前置过滤也可以。

策略：

- 不阻塞每个 family 的第一次 submit。
- 同 family + same mutation_summary + same no-trigger negative evidence >= 3 时拒绝：
  - reason=`blocked_by_negative_evidence`

### 5. 修改 `agent_impl/candidates.py`

在 `_select_candidate_family()` 和 `_candidate_budget_for_stage()` 中考虑：

- active sink role。
- candidate family state。
- negative evidence。
- mapping completeness。
- available unreviewed Top-K candidates。

规则：

- recovery stage 优先 revived / alternative candidate。
- 当前 family cooldown 后，如果 Top-K 有 unreviewed crash_site，则引导 review，而不是继续 BASH variants。

### 6. 修改 `agent_impl/validation.py`

避免重复低信息提交：

- 如果 candidate_required 但 negative evidence 指出同一 mutation_axis 已失败，允许 targeted READ/replan。
- 如果 ready PoC 是同 fingerprint，继续拒绝。
- 如果 ready PoC 新但 same family same axis repeated no-trigger，提示先 revise mapping 或 rotate candidate。

### 7. 修改 `agent_impl/observations.py`

#### `_render_experiments`

显示：

```text
Attempt N: no_trigger
Impact: no crash observed; path reachability is not proven false. Same recipe has 3 no-crash attempts.
Avoid next: do not submit another variant with the same mutation axis until mapping or routing evidence changes.
```

如果是 Task 04 的 sanity checker 拦截：

```text
Pre-submit sanity: FAIL font/otf
Impact: carrier invalid; do not interpret this as sink/path evidence.
Avoid next: do not submit until SFNT table directory is repaired.
```

#### `_render_current_assessment`

`Rejected` 中显示最近 1–3 条 negative evidence。

#### `_render_next_action`

优先级：

1. ready PoC -> submit, unless repeated no-trigger or sanity fail says replan first。
2. pre-submit sanity fail -> repair carrier / seed mutation。
3. repeated no-trigger with unchanged recipe -> revise mapping or rotate candidate。
4. candidate rotation recommended -> READ next Top-K path。
5. missing pair -> READ paired endpoint。
6. mapping gap -> resolve or proceed symbolic。
7. no reviewed sink -> review Top-K。

注意这里要修改当前 v13 行为：现在 `_render_next_action()` 在 ready PoC 存在时立刻返回 `SUBMIT NOW`，这会压过 repeated no-trigger 的 replan 提醒。新逻辑应该是：

```python
if last_sanity_fail_requires_repair(state):
    return repair_carrier_or_seed_mutation
if ready_paths and not repeated_no_crash_requires_replan(state):
    return submit_now
if repeated_no_crash_requires_replan(state):
    return replan_mapping_or_rotate_candidate
```

但第一次 ready PoC 必须仍然快速 submit，避免损害 v13 的主要优势。

### 8. 修改 prompt

文件：

- `agent_prompts/phase/post_submit_miss.md`
- `agent_prompts/phase/verification.md`
- `agent_prompts/system/runtime_context_protocol.md`

新增原则：

- negative evidence is actionable; do not repeat avoid_next。
- no-crash does not refute source-backed path immediately, and must not be called `path_not_reached` without evidence。
- repeated no-crash without recipe change requires replanning。
- rotate candidate only after preserving useful carrier/format facts。

### 9. rollout 脚本/审计

新增：

- `scripts/audit_observation_context.py`

扫描真实 trace：

- six-section compliance。
- old markers。
- negative evidence rendered count。
- repeated avoid_next violation。
- Required Conditions pending rate。
- no-crash after unchanged recipe count。

## 测试

新增：

- `tests/test_negative_evidence.py`
- `tests/test_submit_queue_negative_evidence.py`
- `tests/test_feedback_replanning.py`
- `tests/test_feedback_taxonomy_no_crash.py`
- `tests/test_no_trigger_does_not_refute_path.py`
- 扩展 `tests/test_vnext_context_rendering.py`

## Definition of Done

- smoke 中 non-success avg submits 明显低于 v13。
- repeated no-trigger 后能看到 negative evidence 和 Next Action replan。
- `vul_exit_code=0` 默认显示 `no_crash_unknown`，不会无证据地 refute path gates。
- `path_reached_no_trigger` / `trigger_condition_not_satisfied` 能引导 revise recipe，而不是一直重查路径。
- `budget_after_many_submits` bucket 下降。
- completed crash rate 不低于 v13 baseline。
- 同时报告 `crash/all_started`，防止通过留下 running 假性提升。
