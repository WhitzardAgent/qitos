# Task 01 — 结构化描述分析与代码引用验证

## 目标

让 LLM 对漏洞描述的理解成为 typed、可验证、可恢复的导航输入。完成后，系统不再依赖正则从自然语言中猜函数名；每条描述线索都明确处于 `unverified`、`verified` 或 `unresolved` 状态。

## 非目标

- 不做 embedding/vector search。
- 不因 exact symbol hit 自动确认 sink。
- 不删除所有 deterministic fallback；旧 checkpoint 或模型未调用工具时仍需可运行。

## 数据契约

### 修改 `state.py`

新增：

```python
@dataclass
class DescriptionAnalysis:
    vuln_type: str = ""
    crash_type_hint: str = ""
    access_mode: str = "unknown"       # read | write | free | call | control | unknown
    memory_region: str = "unknown"     # heap | stack | global | container | unknown
    mechanism_tags: List[str] = field(default_factory=list)
    described_operations: List[str] = field(default_factory=list)
    described_state_transitions: List[str] = field(default_factory=list)
    numeric_facts: List[str] = field(default_factory=list)
    suspect_functions: List[str] = field(default_factory=list)
    suspect_files: List[str] = field(default_factory=list)
    suspect_modules: List[str] = field(default_factory=list)
    suspect_params: List[str] = field(default_factory=list)
    trigger_conditions: List[str] = field(default_factory=list)
    search_hints: List[str] = field(default_factory=list)
    status: str = "pending"

@dataclass
class VerifiedCodeRef:
    query: str
    symbol_id: str = ""
    symbol: str = ""
    file: str = ""
    line: int = 0
    match_kind: str = ""   # exact_symbol | casefold_symbol | file | text
    confidence: float = 0.0
    evidence: str = ""
```

在 `CyberGymState` 增加：

- `description_analysis: DescriptionAnalysis`
- `verified_search_refs: List[VerifiedCodeRef]`
- `unresolved_search_hints: List[str]`

在 `__post_init__()` 将 dict 恢复为 dataclass。限制 search hints、refs 各最多 24 条，避免 checkpoint/prompt 膨胀。

这些分类全部是 description prior，不是 sanitizer truth。`mechanism_tags` 必须来自统一枚举，例如：`bounds_read/bounds_write/lifetime_use/lifetime_free/uninitialized_origin/uninitialized_use/integer_wrap/negative_length/null_deref/type_confusion/overlap/resource_progress/format_routing`。

## 代码修改

### 1. `tracking_tools.py`

新增 `AnalyzeDescriptionTool`：

- tool name：`analyze_description`
- 参数显式包含上述 8 个字段；工具不是“无参数工具”，LLM 的结构化判断通过 JSON arguments 传入。
- `validate_input()`：
  - 数组元素必须是非空字符串；逐字段去重、strip。
  - 每个数组最多 12 项，`search_hints` 最多 24 项。
  - `crash_type_hint` 使用 `analysis.vuln_patterns.normalize_crash_type()`。
  - crash type 支持全量 error stack 中的 canonical families，而不只旧的 9 类：buffer under/overflow、UAF/use-after-poison/stack lifetime、double/invalid free、negative-size、uninitialized、SEGV/null、bad-cast/function-pointer、container/object-size、memcpy overlap、UBSan/assertion/UNKNOWN。
  - `access_mode/memory_region/mechanism_tags` 必须在知识库枚举内；未知就保留 unknown，禁止自由造标签。
  - 至少提供 `vuln_type/crash_type_hint` 或任一线索列表，拒绝全空 payload。
- `execute()`：
  - 写入 `state.description_analysis`，status=`recorded`。
  - 若没有 ASAN oracle 结果，仅更新 `metadata["crash_type_prior"]`；绝不覆盖 submit 反馈写入的真实 `state.crash_type`。
  - 设置 `state.metadata["_description_analysis_dirty"] = True`。
  - 返回紧凑摘要，不在 tool output 回显完整描述。

### 2. `agent_impl/tool_registry.py`

- 导入并注册 `AnalyzeDescriptionTool()`，位置放在 `SetCrashTypeTool` 前。
- 保留 `set_crash_type` 一个版本周期，Task 05 再决定是否收敛；两者同时出现时 `analyze_description.crash_type_hint` 是 prior 的最新来源。

### 3. `analysis/service.py`

新增 `verify_description_references(analysis, limit_per_hint=5)`：

1. 对 `suspect_functions` 和 `search_hints` 先查 `self.symbols`：qualified/name exact → casefold exact → token-normalized exact。
2. 对 `suspect_files` 查 `file_hashes` 和 symbol.file 的 basename/suffix。
3. symbol/file 未命中时才在已索引源文件做 bounded literal text search；search hint 必须用 `re.escape()`，不能把 LLM 文本当正则执行。
4. 同一个 `(query, symbol_id/file, line)` 去重。
5. 返回 `status`、`refs`、`unresolved_hints`、`truncated`、`gaps`。索引 partial 时 unresolved 只能标为未知，不能断言不存在。

不要从这里生成 `SinkCandidate`。refs 只进入 scorer 的 description prior。

验证完成后为每条 ref 计算局部 expansion seed：函数 ref 向 callees 扩 3–5 hops、文件/模块 ref 限制候选池，但 expansion 由 Task 03 执行。全量 stack 统计表明 description 一旦提到真实 crash path，绝大多数位于 top 3–5 project frames；因此 ref 应定位局部 path neighborhood，而不是直接被标成 crash site。

### 4. `agent_impl/static_analysis_runtime.py`

新增 `_refresh_description_analysis(state)`：

- 在 `CyberGymAgent.reduce()` 处理完 action results 后、`_run_pending_sink_analysis()` 前调用。
- 仅当 dirty flag 存在时执行，调用 `verify_description_references()`。
- 写入 typed state，并将 `DescriptionAnalysis.status` 设为 `verified` 或 `partial`。
- 以结构化 analysis + verified refs 重跑 `discover_sink_navigation_leads()` 和 `reachable_functions_from_entry()`；Task 03 前可先传 verified symbol IDs 作为 `focus_symbol_ids`。
- 服务不可用时保留 dirty flag 的失败摘要，但不能阻断 phase。

### 5. `agent_impl/prompts.py` 与 `agent_impl/observations.py`

- ingestion guidance 把“人工提取并 GREP”改为要求调用一次 `analyze_description(...)`。
- `Task Context` 增加最多 6 条 verified refs：`query -> symbol @ file:line (match_kind)`。
- unresolved hints 最多显示 4 条，并提示它们不是 negative evidence。
- 仅在 `description_analysis.status == pending` 时显示强提醒；已记录后不重复催促。

### 6. `agent_impl/state_init.py` 与 `task_spec.py`

- bootstrap 仍可计算 legacy `task_spec`，但把其结果标注 `metadata["task_spec_source"] = "deterministic_fallback"`。
- `_generate_sink_candidates()` 不再从 `_symbol_mentions()` 产生可自动晋级的候选。过渡期若保留，必须 `status="provisional"`、confidence ≤ 0.29、`requires_review=True`，且 `_auto_promote_sink()` 不接受该 source。
- 不在本任务删除 `_classify_bug_type()` 等函数；Task 05 执行主路径收敛。

### 7. `analysis/vuln_patterns.py` 与 Task 03 知识库接口

- 扩展 `normalize_crash_type()`，保证 description prior、error-stack evaluator、submit feedback 使用同一 canonical taxonomy。
- Task 01 只负责分类和结构化线索；专业 sink semantics 存在 Task 03 的 `analysis/vulnerability_knowledge.py`，避免 prompt、scorer、parameter mapper 各写一套规则。
- `AnalyzeDescriptionTool` 返回的 `mechanism_tags` 必须能直接查询知识库得到 candidate roles、endpoint families 和 critical args。

## Context 与 Prompt 落地

### `agent_impl/observations.py`

不得新增 `## Description Analysis`。按六段式落位：

- `Mission`：仍只显示原始漏洞描述与 crash/bug type，不重复 refs。
- `Current Assessment > Likely`：最多 6 条 verified refs，格式为 ``query -> symbol @ file:line [source: analysis service]``。
- `Current Assessment > Unknown`：最多 4 条 unresolved hints，并明确“unresolved is not absent”。
- `Current Assessment > Rejected`：stale/contradicted hint 最多保留 3 steps；之后从 observation 消失但保留在 artifact/state。
- `Next Action`：description 尚 pending 时推荐 `analyze_description(...)`；verified 后推荐 READ 最高价值 ref 或等待 Task 03 的 Top-1 path，不继续泛 GREP。

给 `DescriptionAnalysis`/refs 增加 `created_step/last_relevant_step/status` 或等价生命周期字段，renderer 才能执行 TTL；不要从 observation 文本反解析生命周期。

### `agent_impl/ir_renderer.py`

新增纯函数 `render_verified_ref()` 和 `render_unresolved_hint()`，统一 provenance、location 和截断。`AnalysisService` 返回 dict/IR，不能预先生成 Markdown 存进 metadata。

### Prompt resources

- `agent_prompts/phase/ingestion.md`：用 `analyze_description` 替代单独 `set_crash_type` 作为首要动作；要求区分 description prior 与 verified ref。
- `agent_prompts/phase/exploration.md`：删除“auto-detected from description”“broad GREP every keyword”等旧策略；改为从 Current Assessment 的 verified refs 或 Vulnerability Path 选择一个定向 READ。
- `agent_prompts/system/runtime_context_protocol.md`：声明 `[source: description]` 是 prior、`[source: analysis service]` 是 source-backed lead 但不是 confirmed sink。
- `agent_impl/prompts.py::_phase_operating_guidance()`：不要再动态拼一份与 resource 重复的 description/sink 指导；Task 05 统一清理。

### Delta 与 compaction

- description status 从 pending→recorded→verified 时递增 `description` context revision，强制 full brief。
- 之后单个 ref 排名变化只让 Current Assessment 走 delta，除非改变 Top-1 path。
- refs 全部存在 typed state；旧 GREP/tool output 被 snip 后 observation 仍能重建。
- `PostCompactRestorer` 不单独恢复 refs 列表，只清 section hash 并由 full brief 重绘；active verified ref 可进入紧凑 Investigation Brief。

## 测试

### 新增 `tests/test_description_analysis.py`

覆盖：

- tool payload 校验、去重、上限和 crash type normalization。
- state dict round-trip。
- exact symbol、casefold symbol、file suffix、literal text 四种验证。
- search hint 中含正则元字符时按 literal 处理。
- partial index 下未命中返回 unresolved，而非 not-found verdict。
- dirty flag 只触发一次 refresh。
- description ref 不会自动生成 confirmed sink。
- 旧 9 类之外的 UAF-poison、invalid-free、negative-size、bad-cast、overlap、UBSan/UNKNOWN 能稳定 normalize。
- access mode、memory region、mechanism tags 非法值被拒绝；UNKNOWN 不被强行归类为 buffer overflow。

### 修改 `tests/test_agent.py`

- tool registry 包含 `analyze_description`。
- ingestion packet 在 pending 时提示工具，在 recorded 后不再提示。
- submit/ASAN crash type 不会被 description prior 覆盖。

### 修改/新增 context 测试

- `tests/test_vnext_context_rendering.py`：refs 只出现在 Current Assessment，不在 Mission/Path 重复；pending/verified 的 Next Action 正确切换。
- 验证 stale ref TTL、Top-K、标准 provenance 和 description revision 的 full-refresh 行为。
- 模拟 history snip/compaction，确认 refs 仍从 state 重建且 observation 不出现 raw JSON。

## 验收标准

- 给定“USER NAME in PE module”，LLM 可登记 `user_name/userName/USER_NAME/pe_*` hints；验证结果只展示真实存在的引用。
- 模糊或错误函数名不会被自动晋升为 sink。
- `verified_search_refs` 能稳定序列化，且 observation 不超过设定 Top-K。
- 旧 state 没有新增字段时仍能加载。
