# Task 05 — 旧启发式收敛、Context/Prompt 审计、评测与发布

## 目标

在 Task 01–04 稳定后，把旧描述启发式和重复 scorer 从主路径移除；按照 `context_design.md` 收敛六段式 observation、phase prompt、delta/compaction；建立可量化的离线评测与灰度开关，并完成 QitOS bundled copy 验证。

本任务分两次交付：

- **05a（所有实现之前）**：只读 error-stack evaluator、冻结 baseline manifest、project-level split、指标 schema。不得等到 Task 01–04 完成后才补评测。
- **05b（最后）**：旧路径清理、完整 A/B、context audit、feature flag 和 bundled release。

## 代码清理

### 1. `task_spec.py`

- 保留 CVE、显式文件路径、harness shell signal 这类确定性提取。
- `_symbol_mentions()`、`_extract_search_anchors()` 不再向正常 vNext state 写主字段；移动为 `_legacy_*` 或只在 `description_analysis.status == pending` 时 fallback。
- `build_task_spec()` 返回 `source="deterministic_fallback"`，调用方能区分 verified LLM analysis。
- 删除依赖英文 blocklist 维持 precision 的主流程分支；相关测试改为验证 fallback 不会晋级 sink。

### 2. `agent_impl/task_analysis.py`

- `_classify_bug_type()`、`_extract_affected_component()` 仅服务没有 `DescriptionAnalysis` 的旧 state/init fallback。
- description analysis 完成后，`bug_type/affected_component` 的 projection 从 typed analysis 统一生成。
- submit_poc crash type 始终优先于 projection。

### 3. `agent_impl/state_init.py` 与 `agent.py`

- 删除 `_generate_sink_candidates()` 中“描述 token → sink candidate”的主路径。
- `_auto_promote_sink()` 只允许：模型确认、ASAN 反馈、或 source-backed ranked endpoint；不允许 legacy description candidate。
- 删除 `description_symbol` 这类不再生产的 source 分支前，先提供 checkpoint migration：加载时改成 provisional legacy source。
- 将 description/harness/path/mapping refresh 集中在静态分析 runtime mixin，避免 `init_state`、READ handler、reduce 各写一份刷新逻辑。

### 4. `analysis/service.py`

- `reachable_functions_from_entry()` 和 `discover_sink_navigation_leads()` 变成 ranked-path projection/兼容 API，移除各自重复 tokenization 和 keyword scorer。
- crash semantics 统一读取 `vuln_patterns.endpoint_semantics()`。
- 删除不再使用的 private scoring branch，并更新 docstring，避免继续声称字符串 description 是 semantic matching。

### 5. Prompt 与测试断言

- `agent_impl/prompts.py`、`agent_impl/observations.py` 删除旧的 BOOTSTRAP/泛 GREP/重复 sink 菜单文案。
- 测试断言结构字段、状态和 allowed tool，不对整段 prompt 文案做脆弱匹配。

## Context 结构收敛

### 1. `agent_impl/observations.py::_render_observation()`

修复与 `context_design.md` 的现有偏差：

- 移除用户可见 `## Foundation`。Mission 在 delta 模式也可以用 compact 版本保持任务锚点，但标题仍为 `## Mission`。
- 移除 `## Allowed Tools`。允许工具并入 `Next Action` 的最多 2 个具体建议，通用工具策略留在 system prompt。
- `current_sections` 只包含固定六段；TUI metadata 可另存内部字段，但不能拼进 LLM observation。
- `_render_required_conditions()` 的 `## Constraint Analysis Diagnostics` 改为 `### Diagnostics` 或普通 `- diagnostic:` 条目。
- 删除 `_navigation_leads_markdown`、`_analysis_brief_sections` 等预渲染 Markdown 的模型主路径；section renderer 直接消费 typed state/IRRenderer 输出。
- end-to-end 检查不允许 observation 出现 raw dict、`&#x27;`、analysis XML 或内部 fingerprint/revision。

### 2. Delta semantic events

在 `_render_observation()` 深拷贝旧 snapshot 后比较：

```python
_vnext_context_revisions = {
    "description": int,
    "harness": int,
    "path": int,
    "mapping": int,
    "feedback": int,
}
```

- 任一 revision 发生“框架级变化”时 full refresh；普通 section 内容变化走 delta。
- snapshot 写回顺序必须避免 mutable same-reference bug。
- revision 绝不进入 context。
- 每 10 个 delta step 的 periodic full refresh 保留。

### 3. 生命周期与 TTL

新增统一 helper（放在 `agent_impl/observations.py` 或小型 `agent_impl/context_policy.py`，二选一，不复制）：

- `is_context_item_visible(created_step, last_relevant_step, ttl, phase)`
- stale description/rejected path/refuted mapping 等统一按 policy 衰减。
- TTL 只影响展示，不删除 ArtifactStore/source evidence。
- phase transition 可改变可见性，不篡改事实状态。

### 4. `context.py::PostCompactRestorer`

- 保留 vulnerability、ready PoC、last submit 等关键恢复能力，但合并碎片化 restored messages 为一个预算内 Investigation Brief。
- brief 包含 active sink/path、第一 open gate、最多 4 条 concrete input mappings、最近 submit outcome。
- restoration 后清除 `_v13_last_sections/_v13_last_events` 并重置 context revision snapshot，确保下一 observation full regenerate。
- `CyberGymContextHistory._build_span_summary_prompt()` 的固定 sections 加入 `Active Vulnerability Path`、`Concrete Input Constraints`；明确禁止从摘要反向覆盖 typed state。
- raw READ/trace 被 snip 后，通过 state 重建的事实必须仍存在。

### 5. `agent_impl/ir_renderer.py`

成为所有 analysis IR 到 Markdown 的唯一出口：

- 增加 description ref、harness consumption、ranked path、input mapping renderer。
- 统一 provenance 标签、status symbol、location、path ID 和 byte range。
- 删除 service/runtime 中预拼 Markdown 后塞 metadata 的路径。
- renderer 保持纯函数，并对每种 IR 有 snapshot-like 结构测试（断言关键字段，不锁整段措辞）。

## Prompt 设计收敛

### System resources

修改 `agent_prompts/system/runtime_context_protocol.md`：

- 解释固定六段式 brief 与 provenance 信任顺序。
- 明确 Unknown ≠ false、recommended path ≠ confirmed、submit_poc 是 oracle。
- 指示优先完成 Next Action 的单一 blocker，不复制整个 runtime context。

检查 `agent_prompts/system/base_persona.md`、`tool_usage.md`：移除与 phase prompt 重复的动态工作流；system 层只保留稳定策略。

### Phase resources

- `phase/ingestion.md`：`analyze_description` → selected harness → first verified action。
- `phase/exploration.md`：比较 Vulnerability Path，定向 READ Top-1，确认/拒绝 endpoint。
- `phase/investigation.md`：沿 ACTIVE path 解决第一个 gate/mapping gap。
- `phase/formulation.md`：将 Required Conditions 转换成具体 byte layout 并尽早写候选。
- `phase/verification.md`、`post_submit_miss.md`、`reinvestigate.md`：依据 Experiments 修改被 refute 的 path/gate/mapping；oracle 覆盖 prior。
- `candidate_ready.md` 等 controller mode prompt 不得要求与 Next Action 冲突的动作。

### `agent_impl/prompts.py`

- `_phase_operating_guidance()` 不再在 resource 后追加大段重复工具说明和示例；动态 blocker 只由 Next Action 渲染。
- grep 所有 prompt/resource，统一使用 Mission、Current Assessment、Vulnerability Path、Required Conditions、Experiments、Next Action 六个名称。
- 清理 `Constraint Board`、`PoC Requirements`、`Sink Candidates list`、`<code_index_context>` 等旧术语。
- bug guidance 保留 crash-class 方法论，但不重复当前 path/mapping 动态事实。

## Feature flag 与兼容

### 修改 `agent_impl/constants.py`、`cli.py`

增加一个总开关，例如 `CYBERGYM_VNEXT_ANALYSIS`：

- 默认在开发/测试启用。
- 关闭时保留当前 stable 行为，便于 benchmark A/B 和紧急回退。
- 不为 4 个子能力各建永久开关；短期调试开关在发布后删除。

checkpoint 兼容要求：

- 新字段都有默认值。
- dict → dataclass migration 有单测。
- legacy `search_anchors`、`description_symbol` 可读但不参与高权重评分。
- ArtifactStore 找不到旧 path/mapping ID 时返回 gap，不抛异常。

## 评测脚本

### 新增 `scripts/evaluate_vnext_navigation.py`

输入：任务目录列表或 manifest；只运行 init/静态分析，不提交 PoC。输出 JSONL：

- task ID、graph status、files indexed、unresolved calls。
- description hints 数、verified refs 数、false/stale refs 数。
- harness selected、consumption status/pattern、first-hop resolution rate。
- ranked path 数、top path endpoint、score breakdown、path partial rate。
- 若 benchmark 有已知 crash function：Top-1/Top-3/Top-5 hit、路径是否覆盖该 symbol。
- mapping confirmed/inferred/unresolved 数和耗时。
- bootstrap 总耗时、prompt 摘要估算 token 数。

该脚本复用统一 error-stack evaluator，并增加：

- `ExactSinkRecall@1/@3/@5`
- `CrashPathRecall@3/@5`
- `CausalCoverage` 与 UAF/uninitialized/free event-pair coverage
- `GraphDistanceToGT`
- `CandidateFamilyDiversity@5`
- `ReachabilityPrecision@5`（resolved/partial 分开）
- candidate-set miss / ranking-selection miss / gate-mapping miss / conversion miss 四类归因

支持 `--error-root` 指向只含 `data/**/error.txt` 的离线目录。脚本禁止把 error content 写入 agent state、prompt 或运行 workspace。

### 新增 `scripts/audit_observation_context.py`

读取 trace 中最终发给 LLM 的 observation/prompt，输出：

- 一级标题集合/顺序违规与 orphan section 数。
- provenance 缺失率。
- 同一 ref/path/mapping 跨 section 重复率。
- permanent warning、stale/rejected item 超 TTL 次数。
- 每 step observation 字符/token 估算的 P50/P95/max。
- full/delta refresh 比率及 semantic event 漏刷新次数。
- compaction 前后 active path、first blocker、concrete mappings 保真率。
- prompt 引用不存在 section/旧术语的数量。

优先复用现有 trace/artifact loader，不把 trace parsing 复制到多个评测脚本。

脚本必须支持 `--baseline` 与 `--vnext` 输出可比较汇总，不依赖网络或 verification server。

### 可选修改 `scripts/eval_sink_recall.py`

若现有脚本的数据契约适合，复用 manifest/task loading；不要复制任务发现和 ground-truth normalization 逻辑。

## 回归门槛

功能门槛：

- 有 ground truth 的 fixture 集上，Top-3 sink/path recall 不低于 baseline。
- verified refs 的错误命中不会自动确认 sink。
- harness analyzer partial 时 stable 路径仍可运行。
- mapping unresolved 时仍允许 candidate PoC 生成和 submit。
- project-held-out ExactSinkRecall@5 最低 60%，目标 68–76%；CrashPathRecall@5 目标 80–88%。
- 提高 recall 不能靠无限 candidates：模型可见 K 固定为 5，内部 pool 上限固定并报告 precision/diversity。
- C++ qualified/template/operator/virtual/function-pointer 子集单独报告，防止总体平均掩盖最难的 20% symbol shapes。

性能门槛：

- bootstrap wall time 相比 baseline 的中位数增幅目标 ≤ 20%；超过时必须给出缓存/预算修正或明确豁免。
- state 中 ranked paths ≤ 5、refs ≤ 24、mappings ≤ 8。
- observation 新增内容有固定 Top-K，避免上下文随 repo 大小线性增长。

Context 质量门槛：

- observation 一级标题只能来自固定六段，顺序正确；phase 隐藏允许某段缺席。
- source-backed factual line provenance 覆盖率 100%。
- 同一事实不在多个 section 重复；Mission 描述只出现一次。
- compaction 后 active path、第一 blocker、confirmed mappings 保真率 100%。
- P95 observation 预算由基准跑确定并写入评测配置；超过预算的 task 必须能定位到具体 section。
- prompt 对旧 section 名称/不存在 section 的引用为 0。

端到端指标门槛与预测：

- baseline 固定为当前 completed crash 75.4%、sink coverage 41.5%，并保存 task-level manifest，避免样本漂移。
- 保守 completed crash 目标 78–81%，合理目标 81–84%，stretch 85–87%。
- 同时报告 crash/all-started、running、submitted-no-trigger timeout；不允许只优化 completed denominator。
- sink-covered 与 sink-missed 分层报告 crash conversion；若 Recall@5 上升但 active sink selection 不升，归因 prompt/ranking；若 selection 上升但 crash 不升，归因 Task 02/04 gates/mapping。
- 所有预测使用 project-level held-out bootstrap confidence interval；样本过少时不宣称显著提升。

## 测试与验证

### 新增/修改测试

- 新增 `tests/test_vnext_state_migration.py`：旧 checkpoint dict、legacy candidate source、缺失 artifact。
- 新增 `tests/test_vnext_context_rendering.py`：六段式、provenance、唯一映射、phase visibility、TTL、delta/full revision、compaction restore。
- 修改 `tests/test_agent.py`：flag on/off 两条主路径、auto-promote source allowlist。
- 修改 description/navigation tests：旧 regex 只作 fallback。
- 运行 Task 01–04 的全部定向测试。

### 最终命令

```bash
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym \
  python3 -m pytest tests -q

python3 scripts/evaluate_vnext_navigation.py --help

python3 scripts/audit_observation_context.py --help

bash scripts/sync_to_qitos.sh

cd /data/pxd-team/workspace-149/zwq/qitos-cybergym
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym \
  python3 -m py_compile qitos/benchmark/cybergym/agent/agent.py
```

## 发布完成定义

- A/B 报告包含 recall、partial rate、mapping coverage、耗时和 prompt budget。
- Context 审计报告包含六段式合规、重复率、provenance、TTL、delta 和 compaction 保真。
- 默认启用 vNext 后至少一次完整 benchmark 无 state migration/runtime import 回归。
- bundled copy 与 source-of-truth 内容一致。
- 文档和测试不再描述旧 `BOOTSTRAP/VERIFY/ACTION_REQUIRED/update_task_ledger` 模型。
- rollback 只需关闭总开关，不需要回滚 checkpoint。
