# Task 00 — Runtime Correctness Hotfixes

## 目标

在推进 vNext_2 大改之前，先修正两个会直接误导 agent 的 runtime correctness 问题：

1. 同一轮 response 里多个 `submit_poc` 的结果必须逐个保留；一旦任一 PoC 触发 vul-side crash，runtime context 不能被后续 no-crash 结果覆盖成“没有触发”。
2. `NO_TRIGGER` / `vul_exit_code=0` 不能默认分类为 `path_not_reached`。没有 crash 只说明 `no_crash_unknown`；只有在有明确证据时，才能升级为 `path_not_reached` 或 `carrier/dispatch_not_reached`。
3. ranked path / call chain 必须保证方向正确、节点去重；不能把 `LLVMFuzzerTestOneInput` 放在 sink 后面，也不能把同一函数重复放在首尾或中间而不标记 loop。

这三个问题是前置任务。否则后续 Task 03/04/05 会基于错误反馈和错误路径继续优化，指标看起来可能改善，但真实 PoC 构造会被误导。

## 当前代码状态

用户 pull 后当前 HEAD：

```text
4cfa749 fix(feedback): keep crash signal on multi-submit; soften no-crash gate wording
```

这说明 multi-submit crash signal 和 no-crash wording 已经有一版修复。但 vNext_2 仍需把修复要求、回归测试和剩余边界写清，避免未来重构回退。

## 实施状态（2026-07-04）

Task 00 的第一版 hotfix 已落地并通过本地全量回归：

```text
python3 -m pytest tests -q
185 passed
```

实际代码落点：

- `agent.py`
  - 保留 `_reduce_round` / `_crash_latch_round` 的同轮 crash latch；
  - 同轮已有 vul-side crash 时，后续 no-crash submit 只进入 feedback history，不覆盖 `last_verification_result`；
  - miss hypothesis / attempt hint / consecutive-miss reminder 改为中性 no-crash taxonomy，不再默认写成 path miss。
- `agent_impl/feedback.py`
  - `vul_exit_code=0` 的 model-facing verdict 改为 `no_crash_unknown`；
  - `_classify_failed_gate()` 默认返回 `no_crash_unknown`，不再返回 `path_not_reached`；
  - `_refute_matching_gates()` 对 `no_crash_unknown` no-op，避免无证据 refute/question path gates；
  - `FailureRecord` 使用 `FailureType.NO_CRASH_UNKNOWN`。
- `family_runtime.py`
  - 新增 `FailureType.NO_CRASH_UNKNOWN`，并纳入 no-progress signal。
- `agent_impl/observations.py` / `agent_impl/tool_render.py`
  - submit observation 从 “NO_TRIGGER/path” 语义改为 “NO_CRASH/reachability unknown”；
  - consecutive miss 提醒改成“先判别 reachability vs trigger condition”。
- `analysis/service.py`
  - 新增 `_normalize_ranked_path_chain(...)`；
  - fallback `entry_paths` 即使方向不确定也保留进入 normalization；
  - 自动修正可判断的 sink→...→entry 反向路径；
  - 去除连续/非连续重复节点，设置 `loop_detected` 和 `normalization_warnings`；
  - 对被修正路径降级为 `partial_recovered_direction` / `partial_duplicate_nodes` / `partial_invalid_direction` 等状态。
- `analysis/models.py`
  - `RankedVulnerabilityPath` 新增 `normalization_warnings`、`loop_detected`。
- `tests/__init__.py`
  - 固定本仓库 cross-test import，避免外部 `tests` 包抢占。

新增/扩展的 regression tests：

- `tests/test_feedback_no_crash_taxonomy.py`
  - 验证 no-crash 默认是 `no_crash_unknown`；
  - 验证 `no_crash_unknown` 不 refute call-chain gates。
- `tests/test_agent.py`
  - 覆盖同轮 crash 后 no-crash 不能覆盖 active crash result。
- `tests/test_ranked_vulnerability_paths.py`
  - 覆盖反向 fallback path 自动翻正并标 warning；
  - 覆盖重复节点去重、`loop_detected=true`、降级为 partial duplicate path。

## 问题 A：multi-submit 结果覆盖

### 现象

同一轮模型 response 可能并行发起多个 `submit_poc`。如果其中一个先触发 crash，后续另一个返回 no-trigger，旧逻辑可能把 runtime context / `last_verification_result` 覆盖为 no-trigger，导致 agent 自己“发现”已经触发却又在下一轮看到没有触发。

### 设计要求

代码必须满足：

- 每个 submit 结果按 `agent_id + poc_path/content_fingerprint/poc_id` 独立归档。
- `feedback_history` 保留同轮所有 submit 的结果。
- `last_verification_result` 可以是单值，但不能让后续 no-crash 覆盖本轮已确认的 vul-side crash stop signal。
- reduce 处理 action results 时，一旦任一 `submit_poc` 设置 `stop_reason=success`，必须停止处理后续会改写 state 的 submit 结果，或只把它们写入 history 不影响 active stop state。
- `Experiments` 应能显示“同轮多个 submit 中至少一个 crash”，而不是只显示最后一个结果。

### 代码落点

- `submit_tool.py`
  - `_submit_results`
  - `_last_submit_by_agent`
  - `_stash_submit_structured(...)`
  - `get_last_submit_structured(...)`
- `agent.py`
  - `reduce(...)` action result loop
  - `_process_action_result(...)` submit branch
- `agent_impl/feedback.py`
  - `_append_feedback_record(...)`
  - `retain_hot_feedback(...)`
- `agent_impl/observations.py`
  - `_render_experiments`
  - `_render_current_assessment`

### 测试

已新增/扩展：

```text
tests/test_agent.py::test_same_round_no_crash_submit_does_not_overwrite_prior_crash
```

当前已覆盖：

- 同轮两个 submit：第一个 crash、第二个 no-crash，最终 active result 保持 crash。
- 两个 submit 的 `feedback_history` 都保留。
- `last_verification_result` 不允许从 crash 回退到 no-trigger。

后续 Task 05 rollout 前建议继续补：

- 同轮两个 submit：第一个 no-crash、第二个 crash，走完整 `reduce(...)` action result loop。
- `Experiments` 明确显示“同轮多个 submit 中至少一个 crash”。

## 问题 B：NO_TRIGGER 不能默认等于 path_not_reached

### 现象

当前大量 no-trigger / `vul_exit_code=0` 可能包括：

- 路径确实没到；
- outer carrier 没解析；
- dispatch gate 没走对；
- 路径到了但 trigger condition 没满足；
- trigger 太弱，没有 sanitizer crash；
- input 被 parser 修正/截断；
- harness 正常执行但漏洞条件未达成。

因此默认反馈成 `path_not_reached` 会误导 agent 一直回头查路径，而不是检查 trigger mutation / field mapping / carrier validity。

### 新 taxonomy

第一版必须引入保守分类：

```text
no_crash_unknown
carrier_parse_failed
dispatch_not_reached
path_not_reached
path_reached_no_trigger
trigger_condition_not_satisfied
duplicate_candidate
timeout_not_crash
vul_only_triggered
wrong_crash_signature
wrong_crash_location
```

默认规则：

```python
if vul_exit_code == 0:
    return "no_crash_unknown"
```

只有满足证据时才升级：

- `carrier_parse_failed`：server/tool output 明确格式错误、parser 拒绝、pre-submit sanity fail。
- `dispatch_not_reached`：source-backed gate 被明确 refuted，或反馈明确某 dispatch selector 未到。
- `path_not_reached`：有明确证据说明目标 function / path gate 未到达。
- `path_reached_no_trigger`：有 source-backed evidence 表明 carrier/dispatch/path 已满足，但没有 crash；应 revise trigger condition / mutation axis。
- `trigger_condition_not_satisfied`：active mapping/recipe 存在但 no crash；优先修 recipe，不直接换 sink。

无明确证据时，只反馈：

```text
No crash observed. This does not prove the path was not reached.
Revise either routing/dispatch evidence or trigger condition.
```

### 代码落点

- `agent_impl/feedback.py`
  - `_verdict_to_action(...)`
  - `_classify_failed_gate(...)`
  - `_feedback_action_guidance(...)`
  - `_derive_failure_record(...)`
  - `_refute_matching_gates(...)`
- `family_runtime.py`
  - `FailureType`
  - feedback cooldown reason
- `agent.py`
  - miss branch after submit
  - gate refutation logic
- `agent_impl/observations.py`
  - `Experiments`
  - `Current Assessment`
  - `Next Action`
- `agent_prompts/phase/post_submit_miss.md`
- `agent_prompts/phase/verification.md`

### 测试

已新增/扩展：

```text
tests/test_feedback_no_crash_taxonomy.py
```

当前已覆盖：

- `vul_exit_code=0` 默认得到 `no_crash_unknown`，不是 `path_not_reached`。
- no evidence 的 no-crash 不 refute confirmed gates。

后续 Task 05 继续覆盖：

- no-crash + sanity fail -> `carrier_parse_failed` / `carrier_sanity_fail`。
- no-crash + confirmed carrier/dispatch/path recipe -> `path_reached_no_trigger` 或 `trigger_condition_not_satisfied`。
- `Next Action` 对 `no_crash_unknown` 同时允许 revise trigger 和 check path，不单向要求查 path。

## 问题 C：ranked path 方向和重复节点

### 现象

用户分析 trace 发现：

- `arvo11173`：`LLVMFuzzerTestOneInput` 被放在 sink 后面，路径方向反了。
- `arvo11173`：`git__strntol64` 同时出现在首尾。
- `arvo11033`：`apply_recurse_func` 重复出现两次。

这些错误会让 agent 长时间围绕一条未验证/拼接错误的路径迭代。

### 设计要求

所有进入 `state.ranked_vulnerability_paths` 和 `Vulnerability Path` section 的 chain，都必须经过 normalize：

```python
def normalize_ranked_chain(entry_ids, endpoint_id, chain, *, by_id) -> NormalizedChain:
    ...
```

规则：

1. entry function 必须在 chain 首位；如果 chain 反向且可安全判断，则反转。
2. endpoint 必须在 chain 末位；如果 endpoint 在中间，截断到 endpoint。
3. 连续重复节点去重。
4. 非连续重复函数默认保留最短 acyclic prefix；如果确实是递归/loop，必须显式标记 `loop_detected=true`，不能伪装成普通 call path。
5. 如果无法确定方向，`resolution_status` 必须降级：
   - `partial_invalid_direction`
   - `partial_duplicate_nodes`
   - `partial_no_entry_path`
   - `partial_endpoint_not_terminal`
6. invalid/partial path 可以作为 lead，但 `Vulnerability Path` 不能渲染成完整可信 entry→sink path。

### 代码落点

- `analysis/service.py`
  - `_path_to_endpoint(...)`
  - `discover_ranked_vulnerability_paths(...)`
  - 新增 `_normalize_ranked_chain(...)`
- `analysis/models.py`
  - `RankedVulnerabilityPath.resolution_status`
  - `gaps`
  - `loop_detected`
  - `normalization_warnings`
- `agent_impl/static_analysis_runtime.py`
  - `_sync_ranked_paths(...)`
  - 不把 invalid direction path 直接 auto-promote 成 active sink。
- `agent_impl/ir_renderer.py`
- `agent_impl/observations.py::_render_vulnerability_path`

### 测试

已新增/扩展：

```text
tests/test_ranked_vulnerability_paths.py::test_reversed_fallback_path_is_normalized_with_warning
tests/test_ranked_vulnerability_paths.py::test_duplicate_fallback_path_nodes_are_removed_and_marked
```

当前已覆盖：

- entry 出现在末尾的 chain 被反转，或标记 invalid。
- 重复节点被去重/降级，不作为普通完整 path。
- `apply_recurse_func` 这类重复节点模式被标记 `loop_detected=true` 或压缩。

后续 Task 03 继续覆盖：

- endpoint 不在末尾时截断/降级的独立 fixture。
- `LLVMFuzzerTestOneInput` 不允许出现在 sink 后面并仍显示为完整 path。

## Definition of Done

- multi-submit crash 不会被同轮 no-crash 覆盖。
- `vul_exit_code=0` 默认分类为 `no_crash_unknown`，不会默认 refute path gates。
- `Experiments` 和 `Next Action` 不再把所有 no-crash 描述为 `path_not_reached`。
- `ranked_vulnerability_paths` 不输出方向反转/重复节点的完整可信 path。
- arvo11173 / arvo11033 风格 regression fixture 通过。
- 不改变六段式 observation heading。
- 不引入动态分析。
