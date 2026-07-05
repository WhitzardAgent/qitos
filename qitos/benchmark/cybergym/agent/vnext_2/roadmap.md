# CyberGym Agent vNext_2 Roadmap

本轮路线图的目标不是再堆一套分析能力，而是在 v13 已经证明有效的 context/submit-feedback 框架上，针对两个暴露出来的瓶颈做下一版迭代：

1. **sink 定位成功率还不够好**：v13 的 completed crash rate 已经优于 v12，但 GT sink hit-rate 低于 v12。
2. **crash rate 仍有提升空间**：v13 的成功主要来自更强 submit 迭代，但非成功任务消耗步数更高，说明候选路径、条件映射和候选轮换仍有明显损耗。

硬约束：**不引入任何动态分析**。这里的“不引入动态分析”包括不新增本地 fuzzing、coverage、gdb/lldb、sanitizer replay、DBI、symbolic/concolic execution 或目标程序运行探测。现有 `submit_poc` 仍然是 benchmark oracle，不属于本轮新增动态分析能力；它只用于验证候选 PoC 是否触发目标 crash。

`error.txt` / ground truth 只用于离线评测、失败归因和规则回归，绝不进入 agent runtime state、observation、prompt 或任务工作区。

## 0. 当前基线与核心判断

### v12 vs v13 实测结论

当前最公平的 crash rate 口径应以 v13 已结束的 75 条为基准：

| 口径 | trace | done | crash | crash rate |
|---|---:|---:|---:|---:|
| v12 全量已结束 | 135 | 113 | 81 | 71.7% |
| v13 当前已结束 | 98 | 75 | 58 | 77.3% |
| 同任务双方已完成 | 74 | v12=53 / v13=57 | v12=71.6% / v13=77.0% |

因此：**v13 是更好的运行基线**。它的六段式 observation/context 设计和 submit-feedback loop 应保留。

但 sink 中间指标上，v13 明显不如 v12：

| 同任务交集 98 条 | v12 | v13 |
|---|---:|---:|
| with candidates | 98/98 | 88/98 |
| avg candidates | 1.020 | 0.980 |
| ExactSinkRecall@5 | 41.84% | 37.76% |
| CrashPathRecall@5 | 69.39% | 59.18% |
| CausalCoverage@5 | 24.49% | 23.47% |

同任务双方已完成的 75 条里，v13 crash 更好，但 candidate hit 仍弱：

| 双方已完成 75 条 | v12 | v13 |
|---|---:|---:|
| with candidates | 75/75 | 70/75 |
| ExactSinkRecall@5 | 42.67% | 40.00% |
| CrashPathRecall@5 | 68.00% | 60.00% |

轨迹行为上，v13 更持久，但也更容易在非成功任务上烧步数：

| 已完成任务 | v12 | v13 |
|---|---:|---:|
| avg steps | 42.4 | 60.0 |
| first candidate | 7.1 | 8.4 |
| first submit | 16.0 | 16.0 |
| avg submits | 5.63 | 7.21 |
| candidate missing | 2 | 5 |
| submit missing | 0 | 2 |

| 非成功但完成任务 | v12 | v13 |
|---|---:|---:|
| avg steps | 85.4 | 150.9 |
| avg submits | 12.12 | 17.12 |

### 全量任务认知

基于 `ground_truth/error_stack_sinks_v1.jsonl` 的 1507 条 error-stack label：

| 漏洞族 | 数量 | 占比 | 对下一版的意义 |
|---|---:|---:|---|
| bounds / OOB / buffer overflow | 740 | 49.1% | 最大收益来源；要识别 direct array/pointer/typed-reader，不只 memcpy |
| use-of-uninitialized-value | 287 | 19.0% | 当前最容易被“危险 API”规则漏掉；需要 origin/use 双角色候选 |
| SEGV / null / corrupted pointer | 191 | 12.7% | 不能全部按 null-deref；要拆分 null、bad cast、function pointer、corrupted index |
| lifetime UAF / poison / stack lifetime | 152 | 10.1% | 必须 event-pair：invalidation + later use |
| double/invalid free | 33 | 2.2% | sink 常是 project cleanup wrapper，不只是 libc free |
| integer/negative-size | 17 | 1.1% | endpoint 是算术 consumer，不只是 arithmetic expression |
| memcpy overlap | 7 | 0.5% | 需要 range-pair constraint |

这意味着下一版不应只扩大搜索深度，而应把 **crash-class-specific endpoint semantics** 做实：不同漏洞族需要不同的 candidate role、critical argument、event sequence 和 false-positive guard。

## 1. 总体设计方向

vNext_2 的核心假设：

```text
completed crash rate
  <- submit-feedback loop 能及时修正 PoC
  <- Required Conditions 能转成具体字节/字段修改
  <- active path 选择了正确 crash/causal/path anchor
  <- Top-K sink candidates 覆盖真实 crash path
  <- static rules 能召回 crash-class-specific endpoint
```

本轮不推翻 v13，而是做四个收敛：

1. **candidate set 必达**：每个任务早期必须有 Top-K source-backed candidates；不允许 v13 这种 10/98 无候选。
2. **candidate role 明确**：`crash_site`、`causal_site`、`path_anchor`、`dangerous_primitive` 必须分开，不能把 description caller 当 exact sink。
3. **context 只呈现可行动信息**：遵守 `context_design.md` 六段式；新能力必须进入 `Mission / Current Assessment / Vulnerability Path / Required Conditions / Experiments / Next Action`，不能追加独立 section。
4. **失败归因驱动迭代**：把 no-trigger timeout 拆成 candidate miss、active sink selection miss、gate/mapping miss、prompt-to-action miss、submit budget miss。

## 2. 非目标

本轮明确不做：

- 不新增动态分析、coverage、fuzzing、sanitizer replay、gdb/lldb。
- 不把 `error.txt` 放入 runtime prompt 或 state。
- 不新建第二套程序图；继续复用 `analysis/service.py`、`analysis/indexer.py`、`analysis/call_graph.py`。
- 不把更多原始 IR/JSON 直接塞进 observation。
- 不追求完整 taint/SMT/slicing；只做 bounded source-backed static heuristics。
- 不为了提高 completed crash rate 而让大量任务长期 running；必须同时看 `crash/all_started`、timeout、median/P75 steps。

## 3. 子任务规划

建议拆成 5 个任务，按顺序落地。每个任务都必须同时交付代码、context/prompt 接线、离线评测或回归测试。

```text
01 Metric & Failure Taxonomy
        ↓
02 Static Sink Recall v2
        ↓
03 Candidate Protocol & Context Selection
        ↓
04 Static Conditions → PoC Conversion
        ↓
05 Feedback Replanning, Budget, Rollout
```

---

## Task 01 — 指标、同任务对比和失败归因固定

### 目标

先把“哪个改动有效”变成可重复回答的问题。v13 当前最大的风险是 crash rate 更好但 sink hit 变差，如果没有更细的归因，后续容易把 submit loop 的收益误判成 sink 定位收益。

### 要改的文件

1. `scripts/evaluate_trace_sink_hit_rate.py`
   - 保留现有 summary 输出。
   - 增加 per-trace 字段：
     - `first_candidate_step`
     - `first_submit_step`
     - `submit_count`
     - `candidate_sources`
     - `candidate_roles`
     - `candidate_families`
     - `active_sink_function`（如果能从 trace/state 中恢复）
     - `selected_ranked_path_id`
   - 增加 `--subset completed|success|with_candidates|common-task-file`。
   - 增加 `--group-by crash_type|project|status|candidate_role|failure_bucket`。

2. 新增 `scripts/compare_run_versions.py`
   - 输入：
     - `--left-name v12 --left-trace-root ...`
     - `--right-name v13 --right-trace-root ...`
     - `--ground-truth ground_truth/error_stack_sinks_v1.jsonl`
   - 输出：
     - 同任务 all/common completed/common success 的 crash rate。
     - ExactSinkRecall@1/3/5、CrashPathRecall@5、CausalCoverage@5。
     - candidate missing、submit missing、first candidate/submit、avg submit。
     - 每个 task 的 delta：`v13_only_crash / v12_only_crash / both / neither`。

3. 新增 `offline_eval/sink_failure_taxonomy.py`
   - 输入单条 trace candidate + GT row + status。
   - 输出 failure bucket：
     - `no_candidate_recorded`
     - `candidate_set_miss`
     - `gt_in_topk_but_not_active`
     - `active_sink_near_gt_but_no_trigger`
     - `condition_mapping_failure`
     - `submit_not_called`
     - `budget_exhausted_after_many_submits`
     - `success`
   - 不需要完美，但必须让后续 PR 能看趋势。

4. 新增/扩展测试：
   - `tests/test_ground_truth_trace_hit_rate.py`
   - 新增 `tests/test_compare_run_versions.py`
   - 新增 `tests/test_sink_failure_taxonomy.py`

### Context / prompt 影响

此任务不改变 runtime prompt。它只产出离线 evaluator，避免把 ground truth 泄漏进 agent。

### Definition of Done

- 能复现 v12/v13 当前结论：
  - v13 completed crash rate 约 77.3%。
  - common completed v13 > v12。
  - v13 common CrashPathRecall@5 低于 v12。
- 能输出“v13 crash 成功但 sink hit 低”的 task 清单，用于后续规则调试。
- CI 中有小 fixture 测试，不依赖远端 traces。

---

## Task 02 — Static Sink Recall v2：提高 Top-K 覆盖

### 目标

把 v13 的 sink candidate coverage 和 CrashPathRecall 拉回并超过 v12。第一阶段目标：

- `with_candidates`: v13 common 从 `88/98` 提升到 `>=96/98`。
- `CrashPathRecall@5`: common 从 `59.18%` 提升到 `65–70%`。
- `ExactSinkRecall@5`: common 从 `37.76%` 提升到 `43–48%`。

### 设计要点

当前 `analysis/service.py::discover_ranked_vulnerability_paths()` 已经有 entry-forward、verified description、risk-backward、harness alignment 的雏形，但问题是：

- 候选池强依赖 `_navigation_rows()`，risk signal 不足时容易漏掉 direct array/pointer/typed-reader 类 crash site。
- `uninitialized`、`SEGV`、`UAF` 需要 event-pair 或 competing hypotheses，不能只选一个 “best signal”。
- description anchor 经常是 caller/path_anchor，不是 leaf crash_site；需要 anchor-local downstream expansion。
- Top-K 候选太少且候选角色不够强约束，模型容易只确认一个候选。

### 要改的文件

1. `analysis/vulnerability_knowledge.py`
   - 扩展 `EndpointSemantics`：
     - 新增 `role_requirements: dict[str, ...]`，描述每种 role 需要的结构证据。
     - 新增 `event_pair_roles`：例如 UAF 的 `invalidation/use`，MSan 的 `origin/use`，integer 的 `arithmetic/consumer`。
     - 新增 `false_positive_guards` machine-readable 规则，而不只自然语言。
   - 新增函数：
     - `candidate_roles_for_crash_type(crash_type) -> list[str]`
     - `score_endpoint_for_role(signal, role, semantics) -> float`
     - `required_event_pairs(crash_type) -> list[tuple[str, str]]`

2. `analysis/vuln_patterns.py`
   - 将 crash type bucket 统一到 bounds/uninit/lifetime/free/integer/segv/type/overlap/resource。
   - 扩展 `CRASH_TYPE_SINK_HINTS`，尤其是：
     - uninit：`check/match/compare/branch/hash/serialize/initialized/row`
     - SEGV：`lookup/get/dispatch/cast/callback/operator[]/deref`
     - lifetime：`destroy/drop/release/unref/erase/clear/realloc`
   - 注意这些只能是 score boost，不能直接当 sink 事实。

3. `analysis/indexer.py`
   - 增加或补强 source-level risk signals：
     - direct array access：`array_access`
     - pointer arithmetic/deref：`pointer_arithmetic`、`pointer_deref`
     - typed endian read/write：`typed_read`、`typed_write`
     - branch on possibly uninitialized local/field/out-param：`branch_on_uninit`
     - lifecycle invalidation：`free/delete/realloc/unref/release/erase/clear`
     - indirect dispatch：`virtual_call/function_pointer/callback_table`
     - cast/tag use：`bad_cast/type_confusion/tag_dispatch`
   - 每个 signal 必须带 `SourceLocation`、`expression`、`parameter_dependencies`，否则不能进入 high score。

4. `analysis/callee_resolution.py`、`analysis/call_graph.py`
   - 选择性移植 `../tree-sitter-analyzer/tree-sitter-analyzer` 中对 C++ qualified method、operator、template、function pointer table、virtual override 的解析思路。
   - 优先目标不是完整 C++，而是降低 `unresolved_callsites` 对 sink recall 的影响：
     - qualified call `obj.method()` 尽量解析到同名方法候选。
     - `operator T()`、`operator[]`、`operator()` 作为可索引 symbol。
     - 函数指针表/handler table 保留多候选，不随便丢弃。

5. `analysis/service.py`
   - 重构 `discover_ranked_vulnerability_paths()`：
     - 内部保留 `candidate_pool_size=50–80`，输出 Top-5/Top-8。
     - 分通道召回：
       1. `description_local_downstream`
       2. `entry_forward`
       3. `risk_backward`
       4. `structural_hazard`
       5. `cpp_dispatch_expansion`
       6. `event_pair_completion`
     - 不再只对每个 endpoint 选一个 `best_signal`；同一 endpoint 可有多个 role-specific path，但最终按 endpoint+role 去重。
     - 当 Top-K 没有 `crash_site` 或没有 crash-type required role 时，自动 deep tier 到 depth 24。
     - 每条 path 输出：
       - `endpoint_role`
       - `candidate_family`
       - `score_breakdown`
       - `generation_channels`
       - `event_pair_id` / `paired_endpoint`（如适用）
       - `false_positive_guards`
       - `next_read`

6. `analysis/models.py`
   - 如果现有 `RankedVulnerabilityPath` 字段不够，补充：
     - `endpoint_role`
     - `role_score`
     - `event_pair`
     - `diversity_key`
     - `coverage_reason`
   - 保持向后兼容，旧 dict checkpoint 能恢复。

7. `agent_impl/static_analysis_runtime.py`
   - `_sync_ranked_paths()` 保留 Top-5 source-backed candidates，但不要把它们视为 confirmed。
   - 如果 ranked paths 少于 3 或没有 crash-type-compatible role：
     - 设置 `state.metadata["candidate_set_incomplete_reason"]`。
     - 触发下一次 observation full refresh。
   - `static_navigation` candidates 的 metadata 必须含 `candidate_role`、`ranked_path_id`、`generation_channels`。

### Context / prompt 落地

1. `agent_impl/observations.py::_render_vulnerability_path`
   - `Vulnerability Path` 中展示 Top-3/Top-5 时，每个候选必须显示 role：
     - `crash_site`
     - `causal_site`
     - `path_anchor`
     - `dangerous_primitive`
   - 显示一句短 reason，例如：
     - `role=crash_site; signal=array_access; input=len controls index`
   - 不显示 raw score dict，只显示紧凑 breakdown：`reach/risk/input/desc`。

2. `agent_impl/observations.py::_render_current_assessment`
   - `Current Assessment > Likely` 只列 candidate 结论，不复制完整 path。
   - 对 `path_anchor` 明确提示：这是导航点，不是最终 crash site。

3. `agent_impl/observations.py::_render_next_action`
   - 如果 `candidate_set_incomplete_reason` 存在：
     - 推荐 READ Top-1 endpoint 或其 leaf callee。
     - Stop condition 必须是“确认/拒绝 endpoint role 并 record_sink_candidate”。
   - 如果 Top-K 中有 paired event 缺一端：
     - 推荐 READ paired endpoint。

4. `agent_prompts/phase/exploration.md`
   - 明确要求优先比较 Top-K roles：
     - 对 bounds：优先 crash_site/dangerous_primitive。
     - 对 UAF/uninit：必须考虑 causal_site + crash_site pair。
     - 对 description 命名函数：先判断 caller/path_anchor，再查 downstream leaf。

### Definition of Done

- 离线 GT common subset：
  - `with_candidates >= 96/98`
  - `CrashPathRecall@5 >= 65%`
  - `ExactSinkRecall@5 >= 43%`
- `tests/test_ranked_vulnerability_paths.py` 覆盖：
  - description caller downstream leaf。
  - UAF free/use pair。
  - uninitialized origin/use pair。
  - direct array access without unsafe API。
  - C++ operator/qualified method。
- Observation 审计无新增 `##` section、无 raw dict/XML。

---

## Task 03 — Candidate Protocol & Context Selection：让模型真的选中好候选

### 目标

解决 “GT 在 Top-K 里，但模型没有 record/active 到正确 sink” 的转化损耗。v13 当前 first candidate 比 v12 晚，且有 candidate missing；这不是静态分析单独能解决的，还需要工具协议和 prompt 约束。

### 要改的文件

1. `tracking_tools.py::RecordSinkCandidateTool`
   - 扩展参数：
     - `candidate_role`: enum-like string，允许 `crash_site|causal_site|path_anchor|dangerous_primitive|unknown`
     - `ranked_path_id`: 可选，关联 `ranked_vulnerability_paths`
     - `source_span`: 可选 `{file,line,end_line}`
     - `paired_with`: 可选 candidate id/path id，用于 UAF/uninit/integer pair
   - 执行逻辑：
     - 如果 function 匹配现有 `static_navigation` candidate，保留其 `ranked_path_id`、role、score_breakdown。
     - 如果 LLM 只记录 `path_anchor`，不要把它当 `_primary_sink_id()` 的唯一 crash target；Next Action 应继续要求 leaf/paired endpoint。
     - entrypoint suppression 保留。

2. `state.py`
   - 不必大规模迁移，但应让 `SinkCandidate.metadata` 的以下字段成为标准约定：
     - `candidate_role`
     - `ranked_path_id`
     - `generation_channels`
     - `source_span`
     - `paired_with`
     - `reviewed`
     - `selection_status`: `unreviewed|active|rejected|cooldown`
   - 如类型安全需要，可新增小 dataclass `SinkCandidateSelection`，但必须兼容旧 dict。

3. `agent_impl/static_analysis_runtime.py`
   - `_sync_ranked_paths()` 生成的 candidates 默认：
     - `status="candidate"`
     - `source="static_navigation"`
     - `metadata.requires_review=True`
     - `metadata.selection_status="unreviewed"`
   - 不自动清空 `pending_sink_checkpoint`，只有 `record_sink_candidate` 或明确 source-backed review 才能清。

4. `state.py::_primary_sink_id()`（如果当前逻辑只按 confidence 排序）
   - 修改为 role-aware：
     - `crash_site` 优先于 `path_anchor`。
     - source-backed model-reviewed 优先于 static unreviewed。
     - 多次 no-trigger 后降低 active candidate priority，而不是一直锁死。

5. `agent_impl/observations.py`
   - `Current Assessment`：
     - 显示 active candidate，并显示 role。
     - 对 unreviewed static candidates 用 “review candidate” 语言，不叫 confirmed。
   - `Vulnerability Path`：
     - Top-K 每条都带 `record_sink_candidate(..., candidate_role=..., ranked_path_id=...)` 的简短操作提示，但不要过长。
   - `Next Action`：
     - 若没有 model-reviewed candidate，强制推荐 review Top-1/Top-2，而不是泛泛“identify sink”。

6. Prompt 文件：
   - `agent_prompts/phase/exploration.md`
   - `agent_prompts/phase/investigation.md`
   - `agent_prompts/system/runtime_context_protocol.md`
   - 修改重点：
     - “Top-K analysis candidates are leads, not confirmed sinks.”
     - “Use candidate_role; do not promote path_anchor to crash_site without leaf evidence.”
     - “For UAF/uninit, record both sides if visible; one-sided candidate is partial.”

### Context / prompt 落地

必须遵守 `context_design.md`：

- 不新增 `## Sink Candidate Review`。
- 候选 review 状态进入：
  - `Current Assessment`：candidate status/role/provenance。
  - `Vulnerability Path`：Top-K path/active path。
  - `Next Action`：当前唯一 review target。
- TTL：
  - unreviewed static candidates 若 5 step 未被读取，保留 Top-3，其余衰减。
  - rejected candidates 最多显示 3 step。

### Definition of Done

- v13-style trace 中 candidate missing 接近 0。
- first candidate step 从 v13 `8.4` 拉到 `<=7.5`。
- `record_sink_candidate` trace 能恢复 role/path_id，用于离线 evaluator。
- `tests/test_vnext_context_rendering.py` 覆盖：
  - path_anchor 不被渲染为 confirmed crash sink。
  - candidate_role 出现在 Current Assessment / Vulnerability Path。
  - Next Action 指向具体 path_id/file:line。

---

## Task 04 — Static Conditions → PoC Conversion：提高 sink-to-crash 转化率

### 目标

提升 crash rate 的第二个瓶颈不是“有没有 sink”，而是“有了 sink 后能否构造触发条件”。v13 的非成功任务平均 17.12 次 submit、150.9 steps，说明很多任务卡在 no-trigger：候选可能接近，但 Required Conditions / input mapping 没有转成正确 bytes。

本任务仍然只做静态：从 source、harness、corpus/sample 文件中提取条件和字段映射，不运行目标程序。

### 要改的文件

1. `agent_impl/constraint_sinks.py`
   - 根据 `candidate_role` 和 crash family 选择 critical arguments：
     - bounds：base/index/offset/length/capacity/stride/element_width。
     - uninit：value/field/out-param/branch condition。
     - UAF/free：pointer/alias/owner/refcount/invalidation call。
     - integer：arithmetic expression/result consumer。
     - overlap：src/dst/len ranges。
   - 不再对所有参数平均展示；优先展示 PoC 可控参数。

2. `agent_impl/constraint_dataflow.py`
   - 增加 bounded backward binding：
     - sink argument → local variable → struct field → parser read → input offset/selector。
   - 对无法证明的映射输出 structured gap：
     - `unknown_alias`
     - `missing_field_offset`
     - `indirect_dispatch`
     - `requires_format_seed`
   - 保持 partial，不把 unknown 当 false。

3. `analysis/input_mapping.py`
   - 统一 mapping shape：
     - `mapping_id`
     - `sink_candidate_id`
     - `ranked_path_id`
     - `argument_role`
     - `sink_expression`
     - `source_kind`: `direct_offset|struct_field|magic|selector|length_field|symbolic`
     - `offset`
     - `width`
     - `endianness`
     - `constraint`
     - `confidence`
     - `status`
     - `evidence`
   - 这个 shape 由 `IRRenderer.render_input_mapping()` 渲染。

4. `agent_impl/static_analysis_runtime.py::_populate_constraints_from_brief`
   - 将 crash-type critical mappings 放入 `state.active_input_mappings`。
   - 将 trigger-oriented requirements 放入 `state.call_chain_gates` 或 `suggested_constraints`。
   - 对 event-pair 漏半边的任务，不生成虚假的完整 gate；进入 `open_analysis_unresolved_ids`。

5. `agent_impl/ir_renderer.py`
   - 强化：
     - `render_requirement`
     - `render_input_mapping`
     - `render_ranked_path`
   - 禁止 raw dict、HTML escape、XML。

6. `agent_impl/observations.py::_render_required_conditions`
   - 显示顺序：
     1. confirmed concrete mappings。
     2. inferred critical mappings。
     3. open mapping gaps。
     4. reachability/dispatch gates。
     5. refuted conditions（短 TTL）。
   - 总条目保持 12 条以内。
   - 对 corpus-first 场景显示“mutate existing seed field”而不是鼓励从零手写。

7. Prompt / bug guidance：
   - `agent_prompts/phase/formulation.md`
   - `agent_prompts/phase/verification.md`
   - `agent_prompts/bug_guidance/buffer_overflow.md`
   - `agent_prompts/bug_guidance/use_after_free.md`
   - `agent_prompts/bug_guidance/uninitialized_value.md`
   - `agent_prompts/bug_guidance/integer_overflow.md`
   - 增加静态转换原则：
     - bounds：优先改 length/index/capacity mismatch。
     - uninit：优先构造 short/error path，使 producer 不写、consumer 仍读。
     - UAF：构造 object lifecycle sequence，不要只放大 buffer。
     - integer：构造 arithmetic wrap 后进入 allocation/copy/loop consumer。

### Context / prompt 落地

- `Required Conditions` 是唯一展示 byte/field mapping 的 section。
- `Next Action` 若 mapping gap 是第一 blocker，必须给具体目标：
  - `READ file:line for definition of len`
  - `mutate seed offset 0x10 width=4 little-endian`
  - `keep symbolic and submit corpus-mutated seed`
- `Experiments` 中 no-trigger feedback 必须回写到相应 condition：
  - `refuted`
  - `questioned`
  - `needs alternative sink`

### Definition of Done

- 在离线 trace evaluator 中新增 `active_sink_near_gt_but_no_trigger` 的 bucket。
- v13-like smoke 中：
  - `submit_missing=0`
  - non-success completed avg steps 从 `150.9` 降到 `<=120`
  - repeated no-trigger 后能看到 gate/mapping revision，而不是盲目 submit。
- 单元测试覆盖：
  - bounds length/capacity。
  - uninit short path。
  - UAF free/use pair partial。
  - overlap range pair。

---

## Task 05 — Feedback Replanning、预算控制与发布

### 目标

在不引入动态分析的前提下提高 completed crash rate，并防止 v13 的“非成功任务长期烧步数”。核心是把 `submit_poc` 反馈转成静态 replanning，而不是一直在同一错误 sink/condition 上变体喷射。

### 要改的文件

1. `agent_impl/feedback.py`
   - 规范 no-trigger / wrong-signal / too-broad / submit-error 的结构化字段。
   - 对 no-trigger：
     - 如果 active sink 是 `path_anchor`，要求寻找 crash_site/paired endpoint。
     - 如果 active sink 是 crash_site 但 gates 不足，标记 `condition_mapping_failure`。
     - 如果同一 family 连续 miss，生成 `candidate_rotation_recommended`。

2. `family_runtime.py`
   - family cooldown/retire 逻辑 role-aware：
     - 同一 sink+same mutation axis 连续 miss N 次，cooldown。
     - 如果 Top-K 还有未尝试 crash_site candidate，优先 rotate。
     - UAF/uninit pair 只尝试一端时，不要过早 retire，先补 paired endpoint。

3. `submit_queue.py`
   - 增加 submit budget discipline：
     - 每个 family 初始 2–3 次。
     - 有 partial signal 才扩展。
     - 纯 no-trigger 连续 3 次后必须回 investigation/replan。
   - 不阻塞 ready PoC 的第一次提交。

4. `agent_impl/candidates.py`
   - `_select_candidate_family()` 使用：
     - sink role。
     - recent feedback。
     - candidate family diversity。
     - source-backed mapping completeness。
   - `_candidate_budget_for_stage()` 在 endgame 不盲目增加同族提交。

5. `agent_impl/validation.py`
   - candidate_required 不应造成死锁：
     - 如果没有 source-backed sink，但有 static Top-K，允许 READ/review。
     - 如果已有 ready PoC，允许 submit。
   - 当 repeated no-trigger 且 no mapping revision，限制继续 BASH 生成近似重复 PoC。

6. `agent_impl/observations.py::_render_experiments`
   - 实验表必须显示最近反馈对 path/gate/mapping 的影响：
     - `kept`
     - `refuted gate`
     - `rotate candidate`
     - `needs paired endpoint`
   - 不展示 AFL boilerplate。

7. `agent_impl/observations.py::_render_next_action`
   - 优先级调整：
     1. ready PoC -> submit。
     2. repeated no-trigger with no revised condition -> replan active sink/gate。
     3. candidate rotation recommended -> READ/record next Top-K candidate。
     4. mapping gap -> resolve or proceed symbolic。
     5. no reviewed sink -> review Top-K。

### Context / prompt 落地

- `Experiments` 是 feedback 的唯一展示位置。
- 被 feedback 修正后的事实回到 `Current Assessment` / `Vulnerability Path` / `Required Conditions`。
- `Next Action` 只给一个 blocker，避免模型在失败后同时看到“继续 submit”和“重新调查”的冲突指令。

### Definition of Done

- v13-like run 中：
  - completed crash rate 目标：保守 `80–83%`，挑战 `83–85%`。
  - `submitted_no_trigger_timeout_rate` 下降。
  - 非成功 completed avg steps `<=120`。
  - first submit 维持 `<=16`，不能因静态分析变慢。
- 同时报告 `crash/all_started`，防止通过留下 running 提高 completed crash rate。
- 若 crash rate 没升但 sink hit 升了，必须用 Task 01 taxonomy 判断是 mapping/prompt/family budget 问题。

---

## 4. Context 与 Prompt 总验收

每个任务都必须遵守 `context_design.md` 的原则。具体硬约束：

1. observation 一级标题只允许六个：
   - `Mission`
   - `Current Assessment`
   - `Vulnerability Path`
   - `Required Conditions`
   - `Experiments`
   - `Next Action`

2. 新事实必须有 provenance：
   - `[source: submit_poc feedback]`
   - `[source: code reading]`
   - `[source: analysis service]`
   - `[source: model_candidate]`
   - `[source: description]`
   - `[source: bootstrap fallback]`
   - `[source: unresolved]`

3. 新分析能力的闭环必须完整：

```text
static analyzer output
  -> typed state / candidate metadata
  -> six-section renderer
  -> phase prompt consumption rule
  -> Next Action one blocker
  -> compaction restore
  -> end-to-end observation test
```

4. 不允许：
   - raw dict
   - XML analysis block
   - `Foundation`
   - `Allowed Tools`
   - `PoC Byte Layout` 独立 section
   - `Constraint Board` 旧名
   - 永久 WARNING

5. 信息生命周期：
   - unreviewed static candidate：Top-3 可见，5 step 未处理衰减。
   - rejected candidate：最多显示 3 step。
   - no-trigger feedback：保留最近 3 次，但必须压缩为 action impact。
   - active path / first blocker / confirmed mapping：compaction 后必须恢复。

## 5. 预期收益与风险

### 保守预测

如果 Task 02 只把 v13 的 candidate missing 修掉，并把 CrashPathRecall@5 从 `59%` 拉到 `65%`：

- completed crash rate：`77.3% -> 80–82%`
- ExactSinkRecall@5：`37.8% -> 43–45%`
- submit_missing：接近 0

### 合理目标

如果 Task 03/04 能把 Top-K 中的正确 candidate 转成 active sink，并减少 no-trigger 盲目重试：

- completed crash rate：`81–84%`
- CrashPathRecall@5：`68–72%`
- ExactSinkRecall@5：`45–50%`
- non-success avg steps：`150.9 -> <=110`

### 主要风险

1. **静态召回增加但 prompt 选择失败**
   - 迹象：GT in Top-K，但 active sink 不对。
   - 应对：Task 03 role-aware protocol + Next Action 指向具体 path_id。

2. **sink 准了但 PoC 仍 no-trigger**
   - 迹象：active sink near GT，submit 多次 no-trigger。
   - 应对：Task 04 mapping/gate + Task 05 feedback replanning。

3. **候选过多污染 context**
   - 迹象：observation 变长，模型忽略 Top-K。
   - 应对：Vulnerability Path 只显示 Top-3/Top-5，Current Assessment 不重复完整 path。

4. **为了 completed crash rate 留下更多 running**
   - 迹象：completed rate 升但 `crash/all_started` 不升。
   - 应对：所有报告同时看 completed、all_started、timeout、steps。

## 6. 发布顺序

1. 先合 Task 01，只读 evaluator。
2. Task 02 behind feature flag：
   - 环境变量建议：`CYBERGYM_STATIC_SINK_RECALL_V2=1`
   - 默认先在 smoke 上启用。
3. Task 03/04 分别启用：
   - `CYBERGYM_CANDIDATE_PROTOCOL_V2=1`
   - `CYBERGYM_STATIC_MAPPING_V2=1`
4. Task 05 后再考虑默认开启。

每个阶段都跑：

```bash
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym \
  python3 -m pytest tests -q

bash scripts/sync_to_qitos.sh

cd /data/pxd-team/workspace-149/zwq/qitos-cybergym
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym \
  python3 -m py_compile qitos/benchmark/cybergym/agent/agent.py
```

远端 A/B 必须使用相同 task manifest，并报告：

- `crash/completed`
- `crash/all_started`
- `timeout(submitted/no-submit)`
- `running`
- `ExactSinkRecall@1/3/5`
- `CrashPathRecall@5`
- `CausalCoverage@5`
- `candidate missing`
- `first candidate/submit`
- `non-success avg steps`
- observation 六段式审计

## 7. 下一版完成定义

vNext_2 可以认为值得大规模运行，当且仅当：

1. 本地和 bundled copy tests 通过。
2. observation 审计：
   - 六段式合规率 100%。
   - no Foundation / Allowed Tools / raw dict / XML。
3. common smoke 的 sink 指标不低于 v13：
   - `with_candidates >= 98%`
   - `CrashPathRecall@5 >= 65%`
   - `ExactSinkRecall@5 >= 43%`
4. crash 行为不回退：
   - first submit 不晚于 v13。
   - submit_missing 不高于 v13。
   - completed crash rate 不低于 v13 当前 77.3%。
5. 若 sink 指标显著升高但 crash rate 未升，必须先完成 Task 04/05 的失败归因，不允许继续扩大静态召回来掩盖 conversion 瓶颈。
