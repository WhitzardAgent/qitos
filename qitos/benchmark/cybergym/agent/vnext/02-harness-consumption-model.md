# Task 02 — Harness 输入消费模型

## 目标

对已经选中的 harness entry 做函数体级 AST 分析，回答：输入从哪里进入、如何被拆分/路由、是否需要 magic、第一跳调用是什么。结果必须指向源码位置，并直接服务 PoC 布局。

它对 sink recall 的作用是提供可信 entry/first-hop 和 dispatch constraints：高风险但不在 selected harness path 上的函数不能挤占 Top-5；无法解析的 first hop 必须标 partial，不能错误淘汰真实 sink。主要影响指标是 ReachabilityPrecision@5、CrashPathRecall@5 和 submitted-no-trigger timeout rate。

## 数据契约

### 修改 `state.py`

新增：

```python
@dataclass
class HarnessConsumptionEvidence:
    kind: str
    expression: str
    file: str
    line: int
    confidence: float

@dataclass
class HarnessConsumptionModel:
    pattern: str = "unknown"
    patterns: List[str] = field(default_factory=list)
    data_parameter: str = ""
    size_parameter: str = ""
    first_hops: List[str] = field(default_factory=list)
    selector_expression: str = ""
    magic_bytes: str = ""
    temp_file_api: str = ""
    evidence: List[HarnessConsumptionEvidence] = field(default_factory=list)
    status: str = "unresolved"
```

在 `InputFormatModel` 增加 `consumption: HarnessConsumptionModel`。不要再添加平行的 `state.harness_first_hops`；第一跳属于选中 harness 的 consumption model，`HarnessCandidate.direct_calls` 继续保存每个候选的粗粒度结果。

`pattern` 取主模式，`patterns` 保留组合模式。枚举：

- `direct_data_size`
- `temp_file`
- `struct_split`
- `magic_header`
- `multi_api`
- `unknown`

## 代码修改

### 1. 新增 `agent_impl/harness_analyzer.py`

提供纯函数：

```python
def analyze_harness_consumption(
    repo_root: Path,
    source_path: str,
    entry_function: str,
) -> HarnessConsumptionModel:
    ...
```

实现要求：

1. 使用 `analysis.parser.Parser.parse_file()`；语言选择复用 `analysis.language_loader`/文件扩展映射，不直接实例化裸 tree-sitter parser。
2. 在 AST 中按函数名和 candidate line 定位 entry definition；重名时返回 partial gap，禁止随便取第一个。
3. 从函数 declarator 解析 data/size 参数；允许 `uint8_t*`, `char*`, `void*` 等等价类型。
4. 遍历 entry body 的 `call_expression`、`subscript_expression`、`pointer_expression`、binary arithmetic 和 `if/switch`：
   - 参数 data 和 size 共同传入同一 callee：`direct_data_size`。
   - `fopen/fwrite/tmpfile/mkstemp/open/write` 建立文件，再把 path/FILE 交给解析 API：`temp_file`。
   - `data[k]`、`data + k`、consume-provider 调用或多段 length 读取：`struct_split`。
   - entry 前部 guard 中的 `memcmp/strncmp` 或固定 byte comparison：`magic_header`；只有常量可可靠求值时填 `magic_bytes`。
   - input-controlled `if/switch` 分派到两个以上不同业务 callee：`multi_api`，记录 selector expression。
5. first hops 只记录 entry body 直接调用的 repo-local callee；过滤 allocator、logging、assert、fuzzer runtime。callee resolution 复用 AnalysisService 的 callsites/candidates；无法解析时保留原始 callee text 并降低 confidence。
6. 每条结论携带 file/line/expression/confidence；AST error 或 source 不完整返回 `status="partial"`。

7. 输出 first-hop resolution coverage：resolved/ambiguous/unresolved 数。一个 indirect/virtual first hop 不得被当成无调用；应交给 Task 03 的 C++ dispatch expansion。

### 2. `agent_impl/harness.py`

- `_build_input_format_model()` 保留 format/corpus 推断，但在 `harness_resolution.selected_candidate_id` 存在后调用 analyzer。
- 将 `consumption.magic_bytes`（source-backed）以高于 fuzzer-name/corpus 的置信度合并到顶层 `InputFormatModel.magic_bytes`；记录 field provenance。
- `input_path` 的优先级：temp_file → `file_argv`/`temp_file`（按现有枚举最终命名），direct/split/magic/multi → `buffer`。
- analyzer 异常只写 consumption partial，不清空原有 format model。

### 3. `agent_impl/repo_index.py`

- 不在本任务整体重写 regex structural index。
- `_extract_harness_entries()`（或生成 `harness_entries` 的对应代码）优先消费 AnalysisService/AST 的 direct call 信息；regex 结果仅作 parser unavailable fallback，并在 entry record 增加 `call_extraction="ast|fallback"`。
- `HarnessCandidate.direct_calls` 使用 resolved first hops 覆盖 fallback calls，但只更新选中 candidate。

### 4. `agent_impl/state_init.py` 与 `agent.py`

- init：harness resolution 完成后构建 consumption model，再启动 `_bootstrap_analysis_index()`；如果 first-hop resolution 依赖 AnalysisService，则允许 bootstrap 后执行一次 enrichment，避免双重索引。
- READ 导致 `_resolve_harness_candidates()` 改变 selected candidate 时，必须重算 consumption；candidate ID 未变化时不要重复 parse。
- 缓存 key：`graph_id/source_path/entry_function/file_digest`，存入 ArtifactStore 或 AnalysisService store，不用进 metadata 大对象。

### 5. `agent_impl/observations.py`

在 Harness Resolution/Input Format 中显示：

```text
Consumption: magic_header + struct_split
First hops: parse_header, decode_record
Selector: data[0] & 3
Magic: 89 50 4e 47 @ fuzz/png_fuzzer.cc:24
```

最多显示 3 条 evidence。`unknown/partial` 要显示 next action（READ harness body），不能声称 raw input 已确认。

## Context 与 Prompt 落地

### 六段式映射

- `Mission`：harness 确认后只显示一行紧凑摘要，例如 `Input: buffer | Pattern: magic_header+struct_split | Magic: 89504e47`。
- `Current Assessment > Confirmed/Likely`：显示 selected harness、first hops 和最多 3 条带 file:line 的 consumption evidence；Mission 已显示的 magic/pattern 值不在这里重复，只显示其证据状态；AST partial 放到 Unknown。
- `Vulnerability Path`：first hops 作为 entry 后的真实路径节点/候选边，由 Task 03 消费；不能在 Assessment 再渲染完整 chain。
- `Required Conditions`：magic/selector/size minimum 只有转换成 `format_gate/dispatch_gate` 后才显示；不要直接复制 analyzer evidence。
- `Next Action`：unknown/ambiguous 时指定 READ 的 harness file:line 和 stop condition；pattern 已确认后不再反复要求 READ harness。

consumption evidence 确认后详细项在 Assessment 最多保留 3 steps，随后仅保留 Mission 摘要与 Path/Gate 投影。

### `agent_impl/ir_renderer.py`

增加 `render_harness_consumption()`：输入 typed model，输出紧凑摘要或 evidence item；禁止把 dataclass `str()` 直接塞入 observation。

### Prompt resources

- `agent_prompts/phase/ingestion.md`：指导先读 Current Assessment 中 selected harness；只有 unresolved 才 GREP harness entry。
- `agent_prompts/phase/exploration.md`：从 consumption pattern 决定动作：magic/split → 记录对应 gate，multi-api → 解析 selector，temp-file → PoC 仍是 file content。
- `agent_prompts/phase/formulation.md`：提醒 source-backed magic/selector 已在 Required Conditions，构造候选时逐条落实，不能重复猜 input delivery。
- system prompt 不写具体 pattern 列表；模式解释属于 phase prompt/renderer。

### Delta 与 compaction

- selected candidate、pattern、magic 或 selector 改变时递增 `harness` revision，强制 full brief。
- first-hop confidence 小幅变化只更新 Assessment/Path delta。
- consumption typed model 在 state 中持久化；PostCompactRestorer 的 Investigation Brief 只恢复 selected harness + pattern + magic/selector，不恢复完整 evidence。

## 测试

### 新增 `tests/test_harness_analyzer.py`

每种模式至少一个最小 C/C++ fixture，并覆盖组合：

- direct data+size。
- temp file 写入再传 path。
- header guard + offset split。
- switch selector + multiple APIs。
- helper/logging calls 被 first hops 过滤。
- 重名 entry、parser error、unresolved callee 返回 partial。

### 修改 `tests/test_harness_resolution.py`

- selected candidate 才进入顶层 consumption。
- source magic 覆盖低置信 fuzzer-name magic。
- candidate selection 改变会刷新 model。
- dict state round-trip。

### 修改/新增 context 测试

- `tests/test_vnext_context_rendering.py`：同一个 magic 不在 Mission、Assessment、Required Conditions 三处以不同概念重复；只有 gate 投影可进入 Conditions。
- 验证 pattern 的 phase visibility/TTL、harness revision full refresh 和 compaction 后摘要保真。
- 验证 partial analyzer 产生 Unknown + 精确 Next Action，而不是错误的 Confirmed。

## 验收标准

- 模型能从 source span 证明 pattern，而不是仅从 fuzzer 名称猜格式。
- first hops 与 AnalysisService 中 entry 的 resolved outgoing edges 一致；unresolved 调用显式降级。
- analyzer 失败不阻塞 agent，也不擦除 corpus/format fallback。
- observation 给出的 magic/selector 可直接转成 PoC 构造条件。
- harness/first-hop gating 提高候选 reachability precision，但在 partial graph 下不降低 held-out ExactSinkRecall@5；若 precision 上升而 recall 下降，不能通过验收。
