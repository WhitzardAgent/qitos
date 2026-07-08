# Task 04 — Sink 关键参数到输入字节映射

## 目标

在模型调用 `record_sink_candidate` 后，自动找出与 crash type 相关的关键 sink 参数，将它们沿现有跨函数数据流回溯到 harness input；能证明时输出 byte offset/width/endianness，不能证明时输出清晰 gap。

本任务不以 sink recall 为主，而以 `sink-covered -> crash` 转化率为主：正确 sink/path 已在 candidates 中时，减少因 magic/dispatch/value/bounds/lifetime sequence 不完整导致的提交未触发超时。目标是该条件转化率提升 3–6pp。

## 设计边界

- 复用 `AnalysisService.analyze_sink_candidate()`、`trace_value()`、`ConstraintIR.input_mapping`、`ChainGate`。
- 不新建另一套 gate board。
- 不要求所有映射都精确后才允许写 PoC；partial mapping 仍应产生候选所需的定性条件。

## 数据契约

### 修改 `analysis/models.py`

新增：

```python
@dataclass
class InputByteMapping:
    mapping_id: str
    sink_argument: str
    sink_expression: str
    source_parameter: str = ""
    offset_expression: str = ""
    offset: int | None = None
    width: int | None = None
    endianness: str = "unknown"
    transform: str = ""
    constraint: str = ""
    status: str = "unresolved"  # confirmed | inferred | unresolved
    confidence: float = 0.0
    evidence: list[SourceLocation] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
```

在 `SinkAnalysisBrief` 增加 `input_mappings`，保持默认空列表以兼容旧序列化。

### 修改 `state.py`

- `ChainGate` 增加 `input_mapping_id: str = ""`；不要直接复制整份 mapping。
- `CyberGymState` 增加 `active_input_mappings` 的紧凑摘要，最多 8 条。
- `__post_init__()` 兼容旧 gate dict。

## 关键参数选择

### 修改 `analysis/vuln_patterns.py`

使用 Task 03 的 `EndpointSemantics` 返回参数角色：

- bounds write/read：memory API 的 `length_arg`、destination/source pointer；优先 length。
- integer overflow：参与 allocation/index/length arithmetic 的 operands。
- UAF/double-free：deallocation pointer + 后续 use pointer；缺少事件对则 unresolved。
- null dereference：被 dereference 的 pointer 和可能返回 null 的 producer。
- uninitialized：risk signal 的 parameter dependencies/definition gap。
- overlap：source/destination range 与 length，输出两区间交叠约束。
- bad-cast/function-pointer：tag/discriminator、receiver/callback target，而不是硬套 length 参数。

### 修改 `analysis/service.py::analyze_sink_candidate()`

当前实现对命中的 sink call 最多一处、所有 arguments 逐个 `trace_value()`。改为：

1. 从 callsite callee + API model + crash semantics 生成 `critical_arguments`，记录 role 和 selection reason。
2. 只自动 trace 前 3 个关键参数；其他参数仅在 full artifact 中列为 skipped，避免预算爆炸。
3. 若 candidate 指向 wrapper 而危险 call 在其 summary 内，trace 实际危险 call 的 argument；若 candidate 自身是危险 callee，则沿最后一条 path edge 的 bindings trace formal parameter。
4. 每个 trace 结果交给 mapper，而不是只输出自由文本 `origin`。

## Mapping 实现

### 新增 `analysis/input_mapping.py`

提供：

```python
def derive_input_mapping(
    trace: dict[str, Any],
    *,
    harness: HarnessConsumptionModel | dict[str, Any] | None,
    sink_argument: str,
    sink_expression: str,
    constraint: str = "",
) -> InputByteMapping:
    ...
```

仅在 source-backed trace 中识别：

- `data[k]` → offset=k, width=1。
- `*(uint16_t *)(data + k)` / memcpy 到 integer → offset=k, width=2；endianness 只有显式 conversion 或平台无关 helper 可证明时填写。
- `read_le16/read_le32`, `be16toh/ntohl` 等已建模 helper → width/endianness/transform。
- `FuzzedDataProvider::ConsumeIntegral<T>()` → 顺序 consumption；只有前序 consumption width 可证明时计算绝对 offset，否则保存 offset expression。
- harness `struct_split` 的 selector/header consumption 作为基准偏移。
- 跨函数 bindings 中的 `data + offset`、`size - offset` 组合表达式。

禁止：

- 从变量名 `len32` 猜 width。
- 在 native cast 没有显式字节序证据时猜 little endian。
- 把无法解析的 symbolic offset 强转为整数。

### 修改 `analysis/service.py`

- full analysis artifact 存完整 mappings。
- brief 只保留 status != unresolved 的前 4 条，加最多 2 条高价值 unresolved gap。
- `_requirement_from_constraint()` 接受 mapping ID/摘要，并填已有 `input_mapping` 字段。
- 若 mapping 对应 sink trigger requirement，产生/更新 requirement：例如 `bytes[0x10:0x14] controls memcpy.length`，而不是凭空推导具体 exploit value。

### 修改 `agent_impl/static_analysis_runtime.py`

- `_run_pending_sink_analysis()` 同步 `brief.input_mappings` 到 `state.active_input_mappings`。
- `_populate_constraints_from_brief()` 创建/更新 `ChainGate(gate_type="value_gate")`，链接 `input_mapping_id`；按 `(sink_id, mapping_id)` 去重。
- 重新分析同一 sink 时，confirmed 新 mapping 替换 inferred 旧 mapping；unresolved 不得覆盖 confirmed。

### 修改 `agent_impl/ir_renderer.py` 与 `agent_impl/observations.py`

在 Required Conditions 下渲染：

```text
[inferred] memcpy.length <- input[0x10:0x14]
  uint32, endian=unknown; constraint: length > allocation
  evidence: parser.c:88 -> copy.c:142
```

不可证明时：

```text
[unresolved] free.ptr aliases later use.ptr
  gap: indirect field write not resolved; next: trace_value(...)
```

## Context 与 Prompt 落地

### `agent_impl/observations.py::_render_required_conditions()`

Input mapping 只能进入 `Required Conditions`，不能新增 `PoC Byte Layout` 或 `Sink Dataflow` section：

- confirmed mapping 排在 inferred/open gate 之前；同一个 `mapping_id` 只出现一次。
- 展示格式包含 argument、offset/offset expression、width、endianness、constraint、provenance。
- unknown width/endian 必须显式写 `unknown`，不能被 renderer 省略后让模型误认为默认 LE/32-bit。
- 最多 4 条 confirmed/inferred mapping + 2 条关键 unresolved mapping，并与全局 12 conditions 上限共同计数。
- raw trace steps、bindings dict、full evidence list 留 ArtifactStore；observation 最多 2 个 source locations。
- mapping 对应的 gate 被 submit feedback refute 时，原条目以 `✗` 和 repair hint 保留到下一次尝试或最多 5 steps。

`_render_vulnerability_path()` 只在节点旁显示有无 dataflow/value gate，不重复 byte ranges。

### `agent_impl/observations.py::_render_next_action()`

- ACTIVE path 已确认但关键 mapping unresolved：推荐一次具体 `trace_value(function,line,expression)` 或 READ 指定 definition，stop condition 为 offset/width/alias 得到证明或明确保持 symbolic。
- mapping 足够且没有 open gate：推荐按 Required Conditions 构造 PoC，不再继续分析。
- candidate_required 状态下，即使 mapping partial，也允许用 seed mutation/符号 offset 生成早期候选；Next Action 不形成分析死锁。

### `agent_impl/ir_renderer.py`

新增 `render_input_mapping()` 和 mapping-aware `render_requirement()`：

- 统一字节区间表达（例如 `[0x10:0x14)`），避免 inclusive/exclusive 混乱。
- 统一 `confirmed/inferred/unresolved/refuted` 状态符号和 provenance。
- 只渲染 IR，不在 renderer 内推导 offset、endianness 或 exploit value。

### Prompt resources

- `agent_prompts/phase/investigation.md`：优先解决 ACTIVE path 的第一个 critical mapping gap；不要无差别 trace 所有参数。
- `agent_prompts/phase/formulation.md`：逐条把 Required Conditions 转成 PoC layout 注释；confirmed byte mapping 精确落实，symbolic/unknown 项用 seed 保留或做一次定向验证，禁止默认为 little endian。
- `agent_prompts/phase/verification.md`：根据 Experiments 判断是 carrier、dispatch、value 还是 bounds mapping 失败，只修改相关字节；submit oracle 覆盖静态 mapping 假设。
- crash-specific `agent_prompts/bug_guidance/*.md` 只解释关键参数角色，不写动态 offsets 或当前任务结论。

### Delta、生命周期与 compaction

- sink 首次获得 confirmed mapping、mapping 被 refute、offset/width 从 symbolic 变 concrete 时递增 `mapping` revision，强制 full brief。
- 仅 confidence 变化走 Required Conditions delta。
- active mappings 存 typed state；compaction 后 full brief 恢复最多 4 条关键 mapping 和第一 gap。
- span summary 的 `Concrete Input Constraints` 保存 byte range/width/endian/status，不保存 raw trace。

## 测试

### 新增 `tests/test_input_mapping.py`

覆盖：

- direct `data[k]`。
- fixed-width cast，endianness unknown。
- explicit LE/BE helper。
- `data + symbolic_offset` 保持表达式。
- 跨函数 binding 后 offset 累加。
- FuzzedDataProvider 已知/未知顺序 consumption。
- UAF event pair 完整/缺失。
- unresolved mapping 不覆盖 confirmed mapping。

### 修改 `tests/test_interprocedural_analysis.py`

- sink brief 只 trace crash-relevant arguments。
- `input_mappings` 与 requirement/path ID 对齐。

### 修改 `tests/test_agent.py`

- pending sink analysis 同步 mapping 并生成去重 value gate。
- observation 有预算，不重复输出 full trace steps。

### 修改/新增 context 测试

- `tests/test_vnext_context_rendering.py`：mapping 只进入 Required Conditions；Path 只显示 gate status，不重复 byte layout。
- unknown endian/width 必须可见；range 统一为半开区间。
- 验证 12 条全局 cap、mapping 去重、refuted TTL 和 mapping revision full refresh。
- 模拟 compaction 后，active mappings 和第一 gap 从 state 恢复，且不泄漏 raw trace dict。

## 验收标准

- buffer-overflow fixture 能把 memory length 追到具体或 symbolic input offset。
- endianness 和 width 不被无证据猜测。
- UAF 等非单参数漏洞不会被错误简化成 memcpy length 模型。
- mapping 失败不阻塞 candidate creation/submission，并给出下一步查询。
- 在 held-out sink-covered tasks 上单独报告 crash conversion、首次提交命中率和 no-trigger timeout；不能用 sink recall 的提升冒充本任务收益。
