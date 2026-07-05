# vNext_2 任务索引

vNext_2 的目标是把 v13 已经有效的六段式 context 和 submit-feedback loop，推进成 Level 1 专用的完整 PoC campaign loop：

```text
description.txt + repo
  -> runtime feedback/path correctness
  -> static stack surrogate
  -> role-aware candidate review
  -> static-aware classic tools
  -> static PoC recipe
  -> feedback replanning
  -> poc
```

本轮仍然不引入任何动态分析；`submit_poc` 只作为 benchmark oracle 使用，`error.txt` 只用于离线评测。

## 当前 v13 轨迹结论

详见 [`TRACE_FINDINGS_V13.md`](TRACE_FINDINGS_V13.md)。
抽样细读见 [`TRACE_CASE_STUDIES.md`](TRACE_CASE_STUDIES.md)。后者逐条分析了 `tui.log` 工具调用、`assembled_messages.json` 中模型真实看到的六段式 context、静态分析如何进入 `Vulnerability Path` / `Required Conditions`，以及这些细节如何导致 no-trigger、candidate miss 或 late success。

最关键的本地 trace 结论：

- completed：77 条，crash 58，completed crash rate = **75.3%**。
- 非成功 completed：19 条，平均 submit = **15.32**。
- 非成功 completed 里 19/19 都有 candidate，但只有 **47.4%** 命中 GT crash path。
- 主要失败桶：
  - `candidate_set_miss`: 10
  - `budget_after_many_submits`: 5
  - `condition_mapping_failure`: 2
  - `active_near_gt_no_trigger`: 1
  - `submit_not_called`: 1

这说明下一版不能只“鼓励多 submit”，而要同时补：

1. candidate/path recall；
2. role-aware active sink selection；
3. static input/mutation recipe；
4. no-trigger 后的 negative evidence 和 replanning。

抽样 case 给出的额外约束：

- `arvo:17986` / `arvo:19497`：path hit 后 34/49 次 no-trigger，`Required Conditions` 仍是 “candidate conditions filtered as non-actionable”，说明必须做 PoC recipe。
- `arvo:12662`：静态分析已经给出 `ctx->page_header_size-8 >= page_size` 这种触发公式，但 0 submit，说明 formula 没转成 input field mutation。
- `arvo:10013`：错误 active sink 上提交 30 次，说明需要 role-aware candidate rotation。
- `arvo:13249`：29 次 submit 后成功，说明不能粗暴禁止多 submit；要禁止的是 unchanged recipe 的重复提交。

## 子任务

建议按以下顺序实施：

0. [`00-runtime-correctness-hotfixes.md`](00-runtime-correctness-hotfixes.md)
1. [`01-measurement-and-failure-taxonomy.md`](01-measurement-and-failure-taxonomy.md)
2. [`02-role-aware-candidate-protocol.md`](02-role-aware-candidate-protocol.md)
3. [`03-static-stack-surrogate.md`](03-static-stack-surrogate.md)
4. [`06-static-aware-classic-tools.md`](06-static-aware-classic-tools.md)
5. [`04-poc-recipe-and-procedure-memory.md`](04-poc-recipe-and-procedure-memory.md)
6. [`05-feedback-replanning-and-rollout.md`](05-feedback-replanning-and-rollout.md)

## 依赖关系

```text
00 Runtime correctness hotfixes
        ↓
01 Measurement / failure taxonomy
        ↓
02 Candidate protocol v2
        ↓
03 Static stack surrogate
        ↓
06 Static-aware classic tools
        ↓
04 PoC recipe + procedure memory
        ↓
05 Feedback replanning + rollout
```

`00` 是前置 correctness hotfix：先确保 submit feedback、no-crash taxonomy、ranked path 方向/去重正确，否则后续所有指标都会被错误 runtime context 误导。`01` 是只读评测任务，优先做。`02` 可以在 `03` 之前做，因为当前系统已经有 `ranked_vulnerability_paths` 和 `static_navigation` candidates，只是缺少 role-aware review 协议。`03` 提升候选召回，`06` 把静态分析收益投影到模型最常使用的 `GLOB` / `GREP` / `READ`，`04` 提升 sink-to-crash 转化，并吸收专家建议补充通用 PoC 字节/格式 sanity checker，`05` 减少 v13 非成功任务的重复提交消耗。

## 前置正确性反馈

用户基于 `arvo11173`、`arvo11033` 等 trace 额外发现三类必须先修的问题：

- 同一轮多个 `submit_poc` 时，已触发的 crash signal 不能被后续 no-crash 覆盖。
- `NO_TRIGGER` / `vul_exit_code=0` 不能默认解释成 `path_not_reached`；默认应是 `no_crash_unknown`，只有有证据时才细分。
- ranked path 必须做方向和重复节点 normalize，避免 `LLVMFuzzerTestOneInput` 出现在 sink 后面，或 `git__strntol64` / `apply_recurse_func` 这类重复节点被渲染成可信完整路径。

这些要求沉淀在 Task 00，且应在 Task 03 / Task 05 实现前完成或至少有 regression tests 守住。

当前状态（2026-07-04）：Task 00 第一版 hotfix 已实现并通过本地全量回归 `185 passed`。已落地：

- 同轮 crash 后的 no-crash 不再覆盖 active crash result；
- `vul_exit_code=0` 默认进入 `no_crash_unknown`，不会默认 refute path gates；
- observation/tool render 的 no-crash 文案改成 reachability unknown；
- ranked path fallback 会规范化反向路径、去重重复节点，并输出 `normalization_warnings` / `loop_detected`。

剩余 Task 00 边界测试会在 Task 03/05 继续补齐：完整 `reduce(...)` 下 no-crash→crash 的同轮顺序、endpoint 非末尾截断 fixture、以及 Experiments 对“同轮多 submit 至少一个 crash”的更显式展示。

Task 01 当前状态（2026-07-04）：第一版 offline evaluator 已实现。`evaluate_trace_sink_hit_rate.py` 支持 `model_response -> tui.log -> assembled_messages` fallback，输出 action/context/failure-bucket/no-crash/path-normalization 指标；新增 `offline_eval/sink_failure_taxonomy.py` 和 `scripts/compare_run_versions.py`。当前本地 `remote_traces_v13` 同步副本验证为 137 evaluated / 115 completed / 87 success，completed crash rate 75.65%，failure buckets 中 `candidate_set_miss=12`、`condition_mapping_failure=6`、`budget_after_many_submits=5`。

Task 02 当前状态（2026-07-04）：第一版 role-aware candidate protocol 已实现并通过 `195 passed`。`record_sink_candidate` 支持 `candidate_role` / `ranked_path_id` / `source_span` / `paired_with`；static ranked paths 保留为 `requires_review=True` leads；review 后保留 static metadata；observation 的 Confirmed/Likely/Next Action 会显示 role/path/review status；trace evaluator 能抽取 role/path_id。Pair generation、path_anchor downstream enforcement、cooldown/rotation 留给 Task 03/05。

Task 03 当前状态（2026-07-04）：第一版 static stack surrogate 已实现并通过 `201 passed`。`RankedVulnerabilityPath` 增加 role/event-pair/paired endpoint/false-positive guards；`vulnerability_knowledge.py` 增加 crash-family `RoleSchema`、required event pairs 和 role scoring；`indexer.py` 增加 typed write、pointer deref、lifecycle invalidation、branch-on-value、partial initialization、out-param write、bad-cast/dispatch 等静态风险信号；`discover_ranked_vulnerability_paths()` 会输出 role-aware score、uninit `origin+use`、UAF `invalidation+use`、缺 pair gaps，并保留 Task 00 的方向/重复节点 normalization。`ir_renderer.py` 已把 event-pair 和 guard 落到 `Vulnerability Path`。剩余风险是 C++ dispatch/virtual/callback 召回仍需 Task 06 通过 static-aware `GREP` / `READ` 继续补，paired endpoint 也还未转成 PoC recipe，这属于 Task 04/05。

Task 06 当前状态（2026-07-04）：第一版 static-aware classic tools 已实现并通过 `208 passed`。新增 `agent_impl/static_tool_hints.py`；GLOB/GREP/READ 会在不隐藏原始结果的前提下增加 role/path/candidate/family/next-hop lead；repo index 增加 file-level role/parser/risk/format signals；prompt 明确 annotation 只是 lead，wrapper/path_anchor 必须继续跟 downstream；离线 evaluator 增加 high-role GREP/static-aware READ 指标。真实的 candidate-after-read rate 和 GT top-K 改善需在 v14 smoke 验证。

## 新增建议：static-aware classic tools

经典工具增强应作为 Task 06 落地。原则是：不替换 `GLOB` / `GREP` / `READ`，不隐藏原始结果，而是在结果上增加静态分析感知的排序、role annotation 和 next-hop。

重点能力：

- `GLOB`：优先展示 harness、parser、ranked path、description term、seed/corpus 相关文件。
- `GREP`：把命中标注为 `entry` / `parser_gate` / `dispatch` / `path_anchor` / `crash_site` / `causal_site` / `wrapper`。
- `READ`：在读取片段旁边展示 enclosing function、ranked path 关系、nearby risk、next READ / record action。

这条路线的直接指标是降低 `first_candidate_step`、减少 `candidate_set_miss`，并提高 `CrashPathRecall@5`。

## 专家建议：通用 PoC sanity checker

安全专家建议参考 `bmz-q-q/cybergym_agent` 的 `para_action` 分支，以及 `HRsGIT/agentic-poc` 的 `toolbox/font.py` 这类实现，将通用 PoC 构造模板和检查能力补入当前 agent。这个建议应落在 Task 04，而不是作为单独的大系统：

- magic/header 检查；
- hex/offset/简单字段检查；
- corpus seed 对比和 mutation delta 检查；
- format-specific carrier 检查；
- font/SFNT/OTF/CFF2 等通用结构语义检查；
- PoC 模板/动作化构造机制：把“写一个文件”拆成 seed 选择、字段定位、局部 mutation、sanity check、submit 的可审计步骤；
- submit 前诊断“这个 PoC 大概率连 parser carrier 都过不了”。

边界：

- 只检查通用格式/字节结构，不读取 `error.txt`，不运行目标程序。
- 不引入动态分析。
- 检查结果进入 `Required Conditions` / `Experiments` / `Next Action`，不能新增 observation section。
- 如果参考仓库代码不可直接拉取，就按相同接口重写通用 validators。

## 共同验收线

- observation 一级标题仍只允许六段式：
  - `Mission`
  - `Current Assessment`
  - `Vulnerability Path`
  - `Required Conditions`
  - `Experiments`
  - `Next Action`
- 不新增 `Foundation`、`Allowed Tools`、raw dict、XML analysis block。
- 新 runtime 信息必须进入 typed state 或 candidate metadata；不能依赖历史 tool output 未被 compaction。
- 每个任务必须说明自己影响哪个指标：
  - `ExactSinkRecall@K`
  - `CrashPathRecall@5`
  - `CausalCoverage@5`
  - `completed_crash_rate`
  - `submitted_no_trigger_timeout_rate`
  - `no_crash_unknown_rate`
  - `path_normalization_warning_rate`
  - `non_success_avg_steps`
  - `first_useful_read_step`
  - `first_candidate_step`
  - `first_submit_step`
