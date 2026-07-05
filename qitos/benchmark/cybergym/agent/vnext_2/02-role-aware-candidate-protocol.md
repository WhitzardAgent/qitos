# Task 02 — Role-aware Candidate Protocol

## 目标

把 “sink candidate 是一个函数名” 升级为 “模型 review 过的、带 role/path/provenance 的候选”。这是 vNext_2 最快可落地的收益点，因为 v13 已经有 static candidates 和 ranked paths，但缺少稳定的 review 协议。

## 当前证据

本地 v13：

- completed 77 条里，76 条有 candidate。
- success 的 CrashPath@5 = 72.4%，non-success completed 只有 47.4%。
- 非成功 completed 的 candidate 多数只有 1 个，模型缺少 Top-K review/rotation。
- 本地 evaluator 需要从 `tui.log` 和 `assembled_messages` fallback，说明 candidate 记录协议本身也不够结构化。

抽样细读：

- `arvo:10013`：active sink `QuantumTransferMode`，GT path miss，但模型围绕该 sink 记录 gates 并 submit 30 次；context 没有促使 candidate rotation。
- `arvo:12662`：`tui.log` fallback 没有 `record_sink_candidate` action，但 assembled context 已显示 active sink `sas7bdat_parse_page_pass2`；说明 evaluator 和 runtime state 对 candidate 的记录来源不一致。
- `arvo:19497`：uninit 只记录 `isMatchAtCPBoundary` 单点 sink，没有 role/pair，随后 49 次 no-trigger。

## 实施状态（2026-07-04）

Task 02 第一版已落地并通过本地全量回归：

```text
python3 -m pytest tests -q
195 passed
```

实际代码落点：

- `tracking_tools.py::RecordSinkCandidateTool`
  - schema 新增 `candidate_role`、`ranked_path_id`、`source_span`、`paired_with`；
  - 旧调用保持兼容：不传 `candidate_role` 时默认 `crash_site`；
  - 当 `ranked_path_id` 匹配已有 static candidate 时，优先升级该 candidate，不重复新增；
  - review 后保留 static metadata：`score_breakdown`、`generation_channels`、`next_read` 等；
  - 写入标准 metadata：`reviewed`、`selection_status`、`candidate_role`、`ranked_path_id`、`source_span`、`paired_with`、`needs_downstream_endpoint`；
  - `path_anchor` 可记录但不会触发 `_pending_sink_analysis`，也不会设置 `active_sink_candidate_id`。
- `state.py`
  - `SinkCandidate.metadata` 在 `__post_init__` 里补齐默认字段；
  - `_primary_sink_id()` 改为 role-aware priority：
    1. reviewed `crash_site`
    2. reviewed `causal_site` with pair
    3. reviewed `dangerous_primitive`
    4. reviewed `causal_site`
    5. reviewed `unknown`
    6. reviewed `path_anchor`
  - `confirmed_sink_candidates()` 要求非 static provisional、非 `requires_review`、且 `reviewed=True`。
- `agent_impl/static_analysis_runtime.py::_sync_ranked_paths`
  - static ranked paths 现在明确写入：
    `requires_review=True`、`reviewed=False`、`selection_status=unreviewed`、`ranked_path_id`、`candidate_role`、`candidate_family`、`score_breakdown`、`generation_channels`、`next_read`、`normalization_warnings`、`loop_detected`。
- `agent_impl/observations.py`
  - `Current Assessment / Confirmed` 的 sink 行显示 `role=...` 和 `path=...`；
  - `Likely` 中 static candidates 显示为 `Possible sink` / `Lead`，带 role、path、selection status；
  - `Next Action` 对未 review 的 Top ranked path 给出带 `candidate_role` / `ranked_path_id` 的 `record_sink_candidate(...)` stop condition。
- `agent_impl/ir_renderer.py`
  - `render_ranked_path(...)` 显示 `path_id=...`，便于模型复制到 tool call。
- `agent_prompts/system/runtime_context_protocol.md`
- `agent_prompts/phase/exploration.md`
- `agent_prompts/phase/investigation.md`
- `agent_impl/prompts.py`
  - 增加 static lead review 规则：analysis service lead 不是 confirmed sink；review 时带 `candidate_role` / `ranked_path_id`；`path_anchor` 不是 final crash target。
- `scripts/evaluate_trace_sink_hit_rate.py`
  - TraceCandidate 新增 `ranked_path_id`；
  - model_response / tui.log / assembled fallback 会抽取 `candidate_role` 和 `ranked_path_id`；
  - evaluator payload 不再硬编码 role=crash_site。

新增/扩展的 regression tests：

- `tests/test_agent.py`
  - `test_record_sink_candidate_preserves_static_path_metadata_with_role`
  - `test_path_anchor_review_does_not_trigger_active_sink_analysis`
- `tests/test_vnext_context_rendering.py`
  - `test_static_lead_is_likely_not_confirmed_and_next_action_reviews_path`
  - `test_primary_sink_prefers_reviewed_crash_site_over_path_anchor`
- `tests/test_trace_fallback_extraction.py`
  - 验证 tui / assembled fallback 能抽 `candidate_role` 和 `ranked_path_id`。

剩余边界会在 Task 03 / Task 05 继续补：

- reviewed `path_anchor` 后如何强制继续向 downstream endpoint 追踪，而不是长期停在 route node；
- uninit/UAF/integer 的 paired candidate 协议，目前 metadata 已支持 `paired_with`，但 static surrogate 还未系统产生 pair；
- candidate cooldown/rotation 仍留到 Task 05 feedback replanning。

## 具体代码修改

### 1. 修改 `tracking_tools.py::RecordSinkCandidateTool`

扩展参数 schema：

```python
"candidate_role": {
    "type": "string",
    "description": "crash_site | causal_site | path_anchor | dangerous_primitive | unknown"
},
"ranked_path_id": {
    "type": "string",
    "description": "Path id from Vulnerability Path if reviewing a static candidate"
},
"source_span": {
    "type": "object",
    "description": "Optional {file,line,end_line}"
},
"paired_with": {
    "type": "string",
    "description": "Optional candidate/path id for UAF/uninit/integer/overlap pair"
}
```

执行逻辑：

- 如果 `ranked_path_id` 匹配现有 `static_navigation` candidate：
  - 保留该 candidate 的 `generation_channels`、`score_breakdown`、`next_read`。
  - 设置：

```python
metadata["reviewed"] = True
metadata["selection_status"] = "active"
metadata["candidate_role"] = candidate_role
metadata["ranked_path_id"] = ranked_path_id
metadata["source_span"] = source_span
metadata["paired_with"] = paired_with
```

- 如果 `candidate_role == "path_anchor"`：
  - 允许记录。
  - 不应被 `_primary_sink_id()` 当作最终 crash sink 优先选择。
  - 设置 metadata `needs_downstream_endpoint=True`。

- 如果 `candidate_role in {"crash_site", "causal_site", "dangerous_primitive"}`：
  - 可设置 `state.active_sink_candidate_id`。
  - 可触发 `_pending_sink_analysis`。

### 2. 修改 `state.py`

#### `SinkCandidate.metadata` 标准字段

不一定新增 dataclass，但必须约定并在 `__post_init__` 兼容：

```python
candidate.metadata = {
    "candidate_role": "...",
    "ranked_path_id": "...",
    "generation_channels": [...],
    "score_breakdown": {...},
    "next_read": {...},
    "source_span": {...},
    "paired_with": "...",
    "reviewed": bool,
    "selection_status": "unreviewed|active|rejected|cooldown",
    "needs_downstream_endpoint": bool,
}
```

#### 修改 `_primary_sink_id()`

优先级：

1. reviewed `crash_site`
2. reviewed `causal_site` with pair
3. reviewed `dangerous_primitive`
4. reviewed `path_anchor`
5. unreviewed static_navigation

同一 candidate 连续 no-trigger 后，降低优先级或进入 cooldown。

### 3. 修改 `agent_impl/static_analysis_runtime.py::_sync_ranked_paths`

当前 static ranked paths 会生成 `source="static_navigation"` 的 `SinkCandidate`。保留，但补 metadata：

```python
metadata = {
    "requires_review": True,
    "reviewed": False,
    "selection_status": "unreviewed",
    "ranked_path_id": path["path_id"],
    "candidate_role": path["endpoint_role"],
    "candidate_family": path["candidate_family"],
    "score_breakdown": path["score_breakdown"],
    "generation_channels": path["generation_channels"],
    "next_read": path["next_read"],
}
```

不要让 static_navigation 自动满足 sink checkpoint。

### 4. 修改 `agent_impl/observations.py`

#### `_render_current_assessment`

- `Confirmed` 只显示 reviewed/model_candidate。
- `Likely` 显示 unreviewed static candidates。
- 每个 sink 行显示：

```text
Sink: `foo` @file:line role=crash_site path=vpath_x [source: model_candidate]
Lead: `bar` role=path_anchor; needs downstream endpoint [source: analysis service]
```

#### `_render_vulnerability_path`

Top-K path 渲染要包含：

- index
- path_id
- endpoint function/file/line
- endpoint_role
- family
- compact score breakdown
- next_read

#### `_render_next_action`

若没有 reviewed sink，但有 Top-K static path：

```text
Blocking gap: Top ranked vulnerability path has not been reviewed.
Recommended: READ(path="...", offset=..., limit=...)
Target: path_id=vpath_x, role=crash_site
Stop condition: confirm/reject endpoint role, then call record_sink_candidate(..., candidate_role=..., ranked_path_id=...)
```

若 active candidate 连续 no-trigger 且 Top-K 还有未 review candidate：

```text
Blocking gap: active sink has repeated no-trigger without recipe progress.
Recommended: READ next ranked path `vpath_...` and either rotate active sink or record why it is rejected.
Stop condition: active candidate changes, or negative evidence explains why current sink remains best.
```

### 5. 修改 prompt

文件：

- `agent_prompts/system/runtime_context_protocol.md`
- `agent_prompts/phase/exploration.md`
- `agent_prompts/phase/investigation.md`

新增原则：

- Static analysis candidates are leads, not confirmed sinks。
- `path_anchor` 不是 final crash site；必须检查 downstream leaf / dangerous primitive。
- UAF/uninit/integer/overlap 若只记录一端，candidate 是 partial。
- `record_sink_candidate` 时带上 `candidate_role` 和 `ranked_path_id`。

### 6. 测试

新增/扩展：

- `tests/test_record_sink_candidate_protocol.py`
- `tests/test_vnext_context_rendering.py`
- `tests/test_vnext_state_migration.py`

覆盖：

- static candidate 被 reviewed 后 metadata 保留。
- path_anchor 不成为 top active crash sink。
- observation 不把 unreviewed lead 放进 Confirmed。
- Next Action 指向具体 path_id 和 file:line。

## Definition of Done

- v13-like trace 中 candidate missing 接近 0。（需 v14 smoke 验证）
- evaluator 能区分：
  - static candidate
  - reviewed candidate
  - active candidate
- `record_sink_candidate` 的 trace extraction 能拿到 role/path_id。（已实现）
- observation 六段式合规，不新增 section。（全量测试覆盖）
