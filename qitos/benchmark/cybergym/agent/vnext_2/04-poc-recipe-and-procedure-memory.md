# Task 04 — PoC Recipe, Procedure Memory & PoC Sanity Toolbox

## 目标

把 `Required Conditions` 从“条件列表”升级为“PoC mutation recipe”。sink/path 命中后，模型必须知道：

- 用哪个 carrier/seed；
- 保留哪些 format/dispatch gate；
- 修改哪个字段/offset/length/index/lifecycle sequence；
- 还缺哪个静态证据。

同时引入轻量 procedure memory，吸收优秀方案里的 general SOP，但不引入 task-specific GT 或动态分析。

本任务还要吸收安全专家建议：参考 `bmz-q-q/cybergym_agent` 的 `para_action` 分支，以及 `agentic-poc/toolbox`，尤其是类似 `toolbox/font.py` 的通用检查思路，给当前 agent 增加 PoC 构造过程中的模板化动作和静态字节/格式 sanity checker。它用于检查 agent 生成的 PoC 是否满足通用格式要求、magic、hex offset、简单字段、corpus seed mutation 约束，以及 font/SFNT/OTF/CFF2 这类结构语义。该能力不运行目标程序，不属于动态分析。

## 当前证据

本地 v13：

- `condition_mapping_failure`: 2。
- `budget_after_many_submits`: 5。
- `Required Conditions pending contexts = 350`。
- 非成功 completed 平均 submit = 15.32。

说明模型经常在“知道一个近似 sink/path”后，缺少可执行的 input mutation plan。

抽样细读：

- `arvo:17986`：`Required Conditions` 长期是 “candidate conditions were filtered as non-actionable”，但模型继续生成 34 个 JPEG/EXIF 变体。
- `arvo:12662`：`Required Conditions` 已经有 `ctx->page_header_size-8 < 0 || >= page_size`，但没有告诉模型 SAS7BDAT 中哪个 header 字段对应 `page_header_size` / `page_size`，最终 0 submit。
- `arvo:10252`：`Vulnerability Path` 显示 `rst_info_size = 0`，但 `Required Conditions` 仍是 “no PoC-relevant conditions”，导致 10 次 IVF/AV1 no-trigger。
- `arvo:13249`：成功前仍是 “candidate conditions filtered as non-actionable”，靠 29 次 submit 才撞中；recipe 可以降低这种 late success 成本。
- 安全专家指出，当前 PoC 构造缺少 format-aware sanity check 和模板化动作拆解。例如字体类 PoC 需要验证 magic、SFNT/OTF/CFF2 table 结构、字段范围、corpus seed 是否仍保持基本合法；模板化 PoC 构造则应把 seed 选择、字段定位、局部 mutation、sanity check、submit 拆成可审计步骤。当前 repo 虽已有 `FileInfo` / `HexView` / `StructProbe` / `CorpusInspect` 和 `agent_impl/feedback.py::_pre_submit_validate()` 的基础 magic 检查，但没有统一的、多格式可扩展的 pre-submit validator。

## 具体代码修改

### 1. 新增 procedure memory 文件

新增目录：

```text
agent_prompts/procedure_memory/
```

文件：

```text
bounds_overflow_recipe.md
uninitialized_value_recipe.md
lifetime_uaf_recipe.md
integer_size_recipe.md
segv_dispatch_recipe.md
format_carrier_recipes.md
poc_action_templates.md
```

内容要求：

- 只能写 general procedure。
- 不能含 task id、项目名、具体 GT stack、具体 PoC bytes。
- 每个文件控制在 80–160 行以内。

示例主题：

- bounds：preserve carrier, mutate length/index/capacity mismatch。
- uninit：short/error path causes producer skip, downstream consumer branch/copy/use。
- UAF：construct invalidation then later use sequence。
- integer：wrap/underflow feeds allocation/copy/loop consumer。
- segv：classify null vs corrupted index vs bad dispatch。
- PoC action template：seed_select -> locate_field -> mutate_local_bytes -> sanity_check -> submit。

### 2. 修改 `agent_impl/prompts.py`

新增：

```python
def _procedure_memory_guidance(self, state: CyberGymState) -> str:
    ...
```

选择逻辑：

- 根据 `state.bug_type` / `state.crash_type` / `description_analysis.vuln_type` 选择最多 1 个 crash-family recipe。
- 若 `state.poc_strategy in {"corpus_mutate", "binary_python"}`，追加 `format_carrier_recipes.md` 的短节选。
- 插入位置：`task_policy_prompt()`，作为 bug guidance 的补充。

注意：procedure memory 进入 system/phase guidance，不是 observation section。

### 3. 新增 PoC sanity checker 模块

新增文件：

```text
agent_impl/poc_sanity.py
agent_impl/poc_sanity_formats.py
```

优先实现统一接口，再决定是否迁移参考代码：

```python
@dataclass
class PoCSanityIssue:
    severity: Literal["fail", "warn", "info"]
    category: Literal["magic", "size", "offset", "field", "corpus_delta", "format", "font_table"]
    message: str
    evidence: str = ""
    repair_hint: str = ""

@dataclass
class PoCSanityResult:
    path: str
    expected_format: str
    passed: bool
    issues: list[PoCSanityIssue]
    summary: str

def inspect_poc_bytes(path: str, *, expected_format: str = "", seed_path: str | None = None) -> PoCSanityResult:
    ...
```

检查分三层：

1. 通用字节检查：
   - 文件非空，大小没有明显异常；
   - magic/header 是否符合 `state.input_format.magic_bytes` 或 format 推断；
   - printable/binary ratio 是否与 format 预期冲突；
   - recipe 中声明的 offset/width/length 是否越界；
   - 若有 concrete mutation target，PoC bytes 是否真的改到了目标 offset 附近。
2. corpus-aware 检查：
   - seed 与 PoC 的 outer magic/container 是否保持一致；
   - mutation delta 的 offset/length 是否过大；
   - recipe 指定“保留 carrier”时，不能把 header/table directory/chunk skeleton 全破坏；
   - 输出 delta summary，供 `Experiments` 判断“这次是否只是重复同一 mutation axis”。
3. format-aware 检查：
   - PNG/JPEG/PDF/ZIP/WAV/BMP 做轻量 carrier sanity，不做完整 parser；
   - font/SFNT/OTF/CFF2 做专家建议中的通用结构检查：
     - SFNT magic：`0x00010000` / `OTTO` / `ttcf` / `wOFF` / `wOF2`；
     - `numTables`、`searchRange`、`entrySelector`、`rangeShift` 基本一致性；
     - table directory 的 offset/length 均在文件内；
     - OTF 中 `CFF ` / `CFF2` table 是否存在；
     - CFF/CFF2 header length / offSize 基本范围；
     - 不解析完整字体，只判断 carrier 是否足以进入相关 parser。

参考代码迁移边界：

- 可以迁移或重写 magic、hex、简单字段、corpus、SFNT/OTF/CFF2 通用检查；
- 不迁移 task-specific templates、动态执行、调试器、GT stack 或 benchmark 泄漏逻辑；
- 如果 GitHub 参考仓库在运行环境不可访问，就按上述接口重写 validator，保证功能等价而不是阻塞。

### 4. 将 sanity checker 接入 pre-submit validation

修改：

```text
agent_impl/feedback.py::_pre_submit_validate
```

在现有 magic/min-size/toolbox inspect 前后接入：

```python
result = inspect_poc_bytes(
    poc_path,
    expected_format=state.input_format.format or state.poc_strategy,
    seed_path=recipe.get("carrier", {}).get("seed_path"),
)
state.metadata["last_poc_sanity"] = result_to_dict(result)
```

策略：

- `fail` 可以阻塞明显无效 PoC：空文件、完全错误 magic、font table directory 越界、声明 offset 越界。
- `warn` 不阻塞 submit，只写入 `Experiments`，避免因为 PoC 本来就要畸形而过度保守。
- submit 被阻塞时，feedback type 必须是 `carrier_sanity_fail`，不能被误解释为 sink/path 失败。

### 5. 新增/扩展工具：`PoCSanityCheck`

修改：

```text
agent_impl/tools.py
agent_impl/tool_registry.py
agent_prompts/system/tool_usage.md
```

新增工具：

```python
def PoCSanityCheck(path: str, expected_format: str = "", seed_path: str = "") -> str:
    ...
```

模型使用场景：

- 写完二进制 PoC 但 submit 前；
- 使用 corpus seed mutation 后；
- font/SFNT/OTF/CFF2、PDF、ZIP 等 container 结构容易被破坏时；
- no-trigger 且怀疑 parser carrier 没进目标路径时。

输出必须短、结构化，可被六段式 context 摘要：

```text
PoC sanity: FAIL font/otf
- magic: OK OTTO
- font_table: FAIL CFF2 table offset+length exceeds file size
- corpus_delta: WARN 74% bytes changed from seed
Repair: preserve table directory and mutate only CFF2 payload bytes.
```

### 6. 将 sanity result 写入 PoC recipe / context

`state.metadata["poc_recipe"]` 增加：

```python
"sanity_checks": [
  {
    "path": "poc.bin",
    "expected_format": "font",
    "status": "fail|warn|pass",
    "summary": "...",
    "issues": [...]
  }
]
```

渲染规则：

- `Required Conditions`：展示 carrier/sanity checklist，例如“SFNT table directory in-bounds”。
- `Experiments`：展示上一轮 sanity result 和它对下一步的影响。
- `Next Action`：若 sanity fail，优先修 carrier，不要继续提交同一坏文件。
- 不新增第七个 observation section。

### 7. 新增 PoC recipe state shape

初期放在：

```python
state.metadata["poc_recipe"]
```

shape：

```python
{
  "recipe_id": "...",
  "sink_candidate_id": "...",
  "ranked_path_id": "...",
  "carrier": {
    "strategy": "corpus_mutate|binary_python|text|hex",
    "seed_path": "...",
    "reason": "seed satisfies format gates"
  },
  "format_requirements": [...],
  "dispatch_requirements": [...],
  "trigger_mutations": [
    {
      "mapping_id": "...",
      "argument_role": "length|index|offset|pointer|state|selector",
      "source_kind": "direct_offset|struct_field|symbolic|seed_relative",
      "offset": 0,
      "width": 4,
      "endianness": "little|big|unknown",
      "value_strategy": "oversize|negative|wrap|short_chunk|duplicate_free_sequence",
      "constraint": "...",
      "evidence": "file:line ..."
    }
  ],
  "open_gaps": [...],
  "sanity_checks": []
}
```

后续可 dataclass 化，但第一版先保持 metadata 兼容。

### 8. 修改 `analysis/input_mapping.py`

统一 mapping shape：

```python
{
  "mapping_id": "...",
  "sink_candidate_id": "...",
  "ranked_path_id": "...",
  "argument_role": "...",
  "sink_expression": "...",
  "source_kind": "...",
  "offset": ...,
  "width": ...,
  "endianness": "...",
  "constraint": "...",
  "value_strategy": "...",
  "confidence": ...,
  "status": "confirmed|inferred|unresolved|refuted",
  "evidence": "..."
}
```

### 9. 修改 `agent_impl/constraint_sinks.py`

按 crash family 选择 critical arguments：

- bounds：base/index/offset/length/capacity/stride/element width。
- uninit：value/field/out-param/branch condition。
- UAF/free：pointer/alias/owner/refcount/invalidation call。
- integer：arithmetic expression/result consumer。
- overlap：src/dst/len ranges。

不要平均展示所有参数。

### 10. 修改 `agent_impl/constraint_dataflow.py`

实现 bounded static backward binding：

```text
sink argument
  -> local variable
  -> struct field
  -> parser read / input offset / selector
```

无法证明时输出 structured gap：

```text
unknown_alias
missing_field_offset
indirect_dispatch
requires_format_seed
symbolic_only
```

### 11. 修改 `agent_impl/static_analysis_runtime.py::_populate_constraints_from_brief`

从 `analyze_sink_candidate()` 的 brief 同步：

- trigger-oriented requirements -> `call_chain_gates` / `suggested_constraints`
- mappings -> `state.active_input_mappings`
- recipe -> `state.metadata["poc_recipe"]`
- event pair gap -> `open_analysis_unresolved_ids`

### 12. 修改 `agent_impl/ir_renderer.py`

新增或强化：

```python
IRRenderer.render_input_mapping(mapping)
IRRenderer.render_poc_recipe(recipe)
IRRenderer.render_poc_sanity(result)
```

严禁 raw dict / HTML escape / XML。

### 13. 修改 `agent_impl/observations.py::_render_required_conditions`

展示顺序：

1. Concrete mutation targets。
2. Carrier / seed strategy。
3. PoC sanity / carrier checks。
4. Format / dispatch requirements。
5. Trigger conditions。
6. Open mapping gaps。
7. Refuted conditions。

总条目仍限制在 12 条以内。

必须避免的当前坏形态：

```text
- Pending: candidate conditions were filtered as non-actionable.
```

替代形态：

```text
- ? Trigger formula: ctx->page_header_size - 8 >= page_size [source: analysis service]
- ? Mapping gap: locate `page_header_size` and `page_size` in SAS7BDAT header/page records.
- Next mutation target: mutate a valid sas7bdat seed; keep outer header, alter page header size once offset is found.
```

如果只有数值事实，例如 `rst_info_size = 0`：

```text
- ? Candidate trigger: force restoration info size to zero / absent while reaching loop restoration path.
- Mapping gap: identify IVF/AV1 bitstream field controlling rst_info_size.
```

### 14. 修改 prompt

文件：

- `agent_prompts/phase/formulation.md`
- `agent_prompts/phase/verification.md`
- bug guidance files

核心原则：

- 如果 recipe 有 seed_path，优先 mutate seed。
- 如果 concrete offset exists，写 exact bytes。
- 写完 binary/corpus-mutated PoC 后，先运行 `PoCSanityCheck`；如果工具不可用，至少用 `FileInfo` / `HexView` / `StructProbe` 确认 magic、offset、长度字段。
- font/SFNT/OTF/CFF2 任务必须先确认 table directory 和目标 table offset/length 在文件内，再改 payload。
- 如果 symbolic-only，生成少量 variants，然后 submit。
- no-trigger 后先 revise one condition，不要盲目换整套方案。

## 测试

新增/扩展：

- `tests/test_input_mapping.py`
- `tests/test_constraint_analysis.py`
- `tests/test_vnext_context_rendering.py`
- `tests/test_procedure_memory_prompt.py`
- `tests/test_poc_sanity.py`
- `tests/test_poc_sanity_font.py`

覆盖：

- recipe 不新增 observation section。
- procedure memory 不含 task-specific forbidden patterns。
- Required Conditions 渲染 recipe 而非 raw dict。
- concrete mapping 优先于 generic gate。
- 空文件/错误 magic 被 pre-submit fail 阻塞。
- font SFNT/OTF/CFF2 table directory 越界被 fail。
- corpus seed 小范围 mutation 能得到 pass/warn 和 delta summary。
- warning 不阻塞 submit；fail 只阻塞明显 carrier-invalid submit。

## Definition of Done

- `Required Conditions pending` 在 smoke trace 中明显下降。
- path-hit 但 no-trigger 的任务能看到 concrete mutation target 或 structured open gap。
- non-success completed avg submits 下降。
- observation 六段式合规。
- binary/font PoC submit 前能在 `Experiments` 或 `Required Conditions` 看到 sanity check 结果。
