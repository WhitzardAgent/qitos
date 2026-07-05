# Task 03 — Static Stack Surrogate

## 目标

把 `ranked_vulnerability_paths` 做成 CyberGym Level 2 crash stack trace 的静态替代物：在 Level 1 只给 `description.txt + repo` 的情况下，尽可能输出 entry → parser/dispatcher → crash/causal endpoint 的 Top-K source-backed paths。

目标指标：

- completed `CrashPathRecall@5`: 从本地 v13 的 66.2% 提到 70%+。
- non-success completed `CrashPathRecall@5`: 从 47.4% 提到 60%+。
- uninit family `CrashPathRecall@5`: 从 40.0% 提到 55%+。

## 当前证据

失败最多的是 `candidate_set_miss=10`。代表：

- arvo:10013, uninit, candidate `QuantumTransferMode`, path miss。
- arvo:14912, uninit, candidate `MCOperand_getImm`, path miss。
- arvo:18979, segv, candidate `opj_pi_next_lrcp`, path miss。
- arvo:11011, bounds, candidate `lzh_decode_blocks`, path miss。

说明当前候选召回仍偏窄，尤其 uninit / segv / indirect path。

抽样细读补充：

- `arvo:19497` 的 `Vulnerability Path` 后期退化为单节点 `isMatchAtCPBoundary (sink)`，没有 entry-to-sink chain，也没有 uninit origin/use pair。
- `arvo:10013` 的 path 走到 `QuantumTransferMode`，但 GT path miss；它更像 path anchor/dispatch guess，而不是 true crash site。
- `arvo:12662` 的 path 有重复节点：`LLVMFuzzerTestOneInput` 和 `readstat_parse_sas7bdat` 重复出现，说明 path renderer/graph dedupe 要修。
- 用户补充的 `arvo11173` / `arvo11033` 说明不仅是重复节点，还有路径方向错误：
  - `arvo11173` 中 `LLVMFuzzerTestOneInput` 被错误放在 sink 后面；
  - `arvo11173` 中 `git__strntol64` 同时出现在首尾；
  - `arvo11033` 中 `apply_recurse_func` 重复出现两次。
  这类路径必须降级或 normalize，不能渲染成可信完整 entry→sink chain。

## 具体代码修改

### 1. 修改 `analysis/vulnerability_knowledge.py`

扩展 crash-family semantics：

```python
@dataclass(frozen=True)
class RoleSchema:
    role: str  # crash_site | causal_site | path_anchor | dangerous_primitive
    required_signal_kinds: frozenset[str]
    soft_signal_kinds: frozenset[str]
    name_hints: tuple[str, ...]
    critical_arguments: dict[str, tuple[str, ...]]
    false_positive_guards: tuple[str, ...]
```

新增：

```python
def crash_family(crash_type: str) -> str
def role_schemas_for_crash_type(crash_type: str) -> list[RoleSchema]
def score_endpoint_for_role(signal: RiskSignal | dict, schema: RoleSchema) -> float
def required_event_pairs(crash_type: str) -> list[tuple[str, str]]
```

重点规则：

- bounds：array/pointer/memory copy/typed read-write。
- uninit：producer/origin + consumer/use pair。
- lifetime：invalidation + later use pair。
- segv：null/corrupt-index/bad-cast/function-pointer competing hypotheses。
- integer：arithmetic expression + allocation/copy/loop consumer。
- overlap：src/dst/len range pair。

### 2. 修改 `analysis/vuln_patterns.py`

- 统一 crash family normalization。
- 扩展 `CRASH_TYPE_SINK_HINTS`：
  - uninit: `check`, `match`, `compare`, `branch`, `hash`, `serialize`, `init`, `row`
  - segv: `lookup`, `get`, `dispatch`, `cast`, `callback`, `operator`, `next`
  - lifetime: `destroy`, `drop`, `release`, `unref`, `erase`, `clear`, `realloc`
- 这些只做 score boost，不直接产生 confirmed sink。

### 3. 修改 `analysis/indexer.py`

补结构风险信号：

```text
array_access
pointer_arithmetic
pointer_deref
typed_read
typed_write
branch_on_value
partial_initialization
out_param_write
lifecycle_invalidation
indirect_call
virtual_call
bad_cast_or_tag_dispatch
```

每个 `RiskSignal` 必须包含：

- `kind`
- `expression`
- `location`
- `severity`
- `parameter_dependencies`
- `reason`

优先实现 C/C++ tree-sitter 结构简单可识别的模式，不做完整 taint。

### 4. 修改 `analysis/callee_resolution.py` / `analysis/call_graph.py`

选择性移植 `../tree-sitter-analyzer/tree-sitter-analyzer` 的 C++ 解析思路：

- qualified method call。
- `operator[]` / `operator()` / conversion operator。
- virtual override 多候选。
- function pointer table / callback table 多候选。

目标不是完整精确，而是把 unresolved 当 partial path，不要直接丢候选。

### 5. 重构 `analysis/service.py::discover_ranked_vulnerability_paths`

改成多通道召回：

```text
description_local_downstream
entry_forward
risk_backward
structural_hazard
event_pair_completion
cpp_dispatch_expansion
```

实现要求：

- 内部 pool 保留 50–80 个。
- fast depth = 8；Top-K 不足或只有 path_anchor 时，deep depth = 24。
- 同一 endpoint 可有多个 role-specific candidate，但最终按 endpoint+role 去重。
- event-pair 可保留两个 endpoint。
- 输出 Top-K 时 diversity：
  - 每个 file 最多 2 个。
  - 每个 role 最多 3 个。
  - 尽量保留至少 1 个 crash_site。

输出每条 path：

```python
{
  "path_id": "...",
  "endpoint_role": "crash_site",
  "candidate_family": "bounds",
  "generation_channels": [...],
  "score_breakdown": {
    "reach": ...,
    "risk": ...,
    "input": ...,
    "desc": ...,
    "harness": ...,
    "role": ...,
    "penalty": ...
  },
  "chain": [...],
  "endpoint": {...},
  "paired_endpoint": {...},
  "false_positive_guards": [...],
  "next_read": {...}
}
```

Path 后处理必须新增：

- 所有 chain 进入 state 前必须调用 `normalize_ranked_chain(...)`。
- entry function 必须在首位；如果 chain 明显反向且可安全判断，则反转。
- endpoint/crash candidate 必须在末位；如果 endpoint 出现在中间，则截断到 endpoint 或降级。
- 去除连续重复 node。
- 同一 function 重复出现时保留最短 acyclic prefix，除非显式标记 loop。
- 如果只找到 endpoint，没有 entry chain，`resolution_status` 必须是 `partial_no_entry_path`，不能渲染成完整 Vulnerability Path。
- 如果方向无法确认，`resolution_status` 必须是 `partial_invalid_direction`。
- 如果重复节点无法安全压缩，`resolution_status` 必须是 `partial_duplicate_nodes`，并写入 `normalization_warnings`。
- uninit/lifetime 若缺 paired endpoint，在 `gaps` 里写 `missing_origin_endpoint` / `missing_use_endpoint`。

### 6. 修改 `analysis/models.py`

扩展 `RankedVulnerabilityPath`，字段保持向后兼容：

- `endpoint_role`
- `candidate_family`
- `role_score`
- `event_pair`
- `diversity_key`
- `false_positive_guards`

### 7. 修改 `agent_impl/static_analysis_runtime.py`

- `_sync_ranked_paths()` 保存 Top-K 到 state。
- 如果 Top-K 缺 crash-family required role，写：

```python
state.metadata["candidate_set_incomplete_reason"] = "no_crash_site_role|no_event_pair|..."
```

- 触发 context revision `path += 1`。

### 8. 测试

扩展：

- `tests/test_ranked_vulnerability_paths.py`
- `tests/test_ranked_path_normalization.py`
- `tests/test_interprocedural_analysis.py`
- `tests/test_constraint_analysis_advanced.py`

新增 fixtures：

- description names caller, crash leaf is callee。
- uninit origin/use pair。
- UAF invalidation/use pair。
- direct array access without unsafe API。
- C++ operator/qualified method。
- arvo11173-style reversed chain：entry 出现在 sink 后面。
- arvo11173-style repeated endpoint：同一 function 出现在首尾。
- arvo11033-style repeated recursive-looking node：重复节点必须压缩或标记 loop。

## Context / prompt 落地

`Vulnerability Path` section 必须显示 role/family/path_id/next_read。  
`Current Assessment` 只显示候选摘要，不重复完整 path。  
`Next Action` 指向 Top-K 中最需要 review 的 endpoint。

## 当前实现状态（2026-07-04）

第一版 static stack surrogate 已落地，并通过本地全量回归：

```bash
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym python3 -m pytest tests -q
# 201 passed
```

已实现内容：

- `analysis/models.py`
  - `RankedVulnerabilityPath` 增加 `role_score`、`event_pair`、`diversity_key`、`false_positive_guards`、`paired_endpoint`。
- `analysis/vulnerability_knowledge.py`
  - 增加 `RoleSchema`、`crash_family()`、`role_schemas_for_crash_type()`、`score_endpoint_for_role()`、`required_event_pairs()`。
  - 覆盖 bounds、uninit、lifetime/UAF、integer、pointer/segv、dispatch、overlap、resource_progress。
  - uninit 明确区分 `origin + use`；lifetime 明确区分 `invalidation + use`。
- `analysis/vuln_patterns.py`
  - 扩展 uninit / lifetime / segv 关键词，用于 score boost，不直接确认 sink。
- `analysis/indexer.py`
  - 增加静态风险信号：`typed_write`、`pointer_dereference`、`bad_cast_or_tag_dispatch`、`lifecycle_invalidation`、`indirect_call`、`branch_on_value`、`partial_initialization`、`out_param_write`、`field_access` 等。
- `analysis/service.py`
  - `discover_ranked_vulnerability_paths()` 接入 role-aware scoring。
  - 同一 endpoint 支持 role-specific candidate。
  - 输出 `event_pair` / `paired_endpoint` / `false_positive_guards` / `role` score。
  - uninit/UAF 缺少 paired endpoint 时写入 `missing_*_endpoint` gap。
  - 修复 `_event_role_for_signal()` 中 `"uninitialized"` 被 `"init" in text` 误判为 origin 的问题；现在使用 token 级 init 匹配。
  - 保留 Task 00 path normalization：方向反转恢复、重复节点压缩、endpoint terminal 校验、loop 标记。
- `agent_impl/static_analysis_runtime.py`
  - `_sync_ranked_paths()` 将缺 crash-site role 或缺 event pair 的 Top-K 标记为 `state.metadata["candidate_set_incomplete_reason"]`，供后续 context / feedback replanning 使用。
- `agent_impl/ir_renderer.py`
  - `Vulnerability Path` 渲染 `path_id`、role/family、event-pair、paired endpoint、false-positive guard。

新增/更新测试：

- `tests/test_ranked_vulnerability_paths.py`
  - reversed path normalization。
  - duplicate node normalization。
  - uninit `origin + use` event pair。
  - UAF `invalidation + use` event pair。
  - event-pair/guard 渲染。
  - `candidate_set_incomplete_reason` 同步。
- `tests/test_vulnerability_knowledge_roles.py`
  - crash family / required event-pair 映射。
  - uninit branch-use signal 优先打到 `crash_site` role。
- 既有 `tests/test_interprocedural_analysis.py` 回归确认 lifecycle 类信号仍以 `lifecycle` role 展示。

### 指标预期

这版 Task 03 主要提升候选集合质量，而不是直接改变 submit 策略。预期影响：

- `CrashPathRecall@5`：应优先改善 uninit / UAF / segv-dispatch 任务，预计本地 v13 completed 基准上 +3–6pp。
- `candidate_set_miss`：对“描述命中 caller / static path 停在 anchor”的任务，应减少 20–30%。
- `first_candidate_step`：短期变化不大；Task 06 static-aware `GREP` / `READ` 落地后才应明显下降。
- `completed_crash_rate`：单独 Task 03 预计小幅提升，主要通过减少错误 active sink；真正 sink-to-crash 转化还依赖 Task 04 recipe/sanity 和 Task 05 replanning。

### 剩余风险 / 后续交给 Task 06/04/05

- C++ qualified method、operator、virtual override、callback table 的解析仍未完整移植；当前主要靠现有 call resolution 和风险信号补强。这个风险应在 Task 06 static-aware classic tools 和后续 interprocedural fixtures 中继续压低。
- `event_pair` 当前只保留最强 paired endpoint，不做完整 pair path stitching；对多阶段 init/free/use 链条仍可能过窄。
- `candidate_set_incomplete_reason` 已进入 metadata，但还未完整进入 feedback replanning 的 candidate rotation 逻辑；这属于 Task 05。
- `false_positive_guards` 已渲染，但还未转化成可执行 PoC mutation recipe；这属于 Task 04。

## Definition of Done

- 本地 v13-style eval：
  - completed CrashPath@5 >= 70%。
  - non-success completed CrashPath@5 >= 60%。
  - uninit CrashPath@5 >= 55%。
- Top-K path 不只是一组 wrapper/caller。
- Top-K path 不包含未标记的方向反转或重复节点。
- observation 无 raw dict/XML/新 section。
