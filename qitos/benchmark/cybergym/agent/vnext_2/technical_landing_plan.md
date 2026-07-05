# vNext_2 Technical Landing Plan

本文回答一个更直接的问题：在当前 `CyberGymAgent` 的真实实现流程上，下一版到底应该怎么改，才能同时提高 sink 定位成功率和最终 completed crash rate。

结论先行：

> 不要把下一版做成“更大的静态分析器”。要把它做成一个 **static stack surrogate + role-aware candidate protocol + static-aware classic tools + PoC recipe / sanity toolbox + feedback replanning** 的闭环。

也就是说，用静态分析和 context 设计模拟 CyberGym Level 2 stack trace 带来的定位帮助，但仍保持 Level 1 runtime 不泄漏 `error.txt`、不读取 patch、不新增动态分析。

但在进入这些能力建设前，必须先做一组 runtime correctness hotfix：

- multi-submit 同轮结果不能让后续 no-crash 覆盖前面的 vul-side crash；
- `vul_exit_code=0` 默认只能表示 `no_crash_unknown`，不能无证据解释为 `path_not_reached`；
- ranked path 必须方向正确、去重，并把无法确认方向/重复节点的路径降级为 partial lead。

这组前置修复对应 [`00-runtime-correctness-hotfixes.md`](00-runtime-correctness-hotfixes.md)。

2026-07-04 更新：Task 00 第一版 hotfix 已实现，`python3 -m pytest tests -q` 通过 `185 passed`。后续 vNext_2 任务可以基于新的 `no_crash_unknown` taxonomy、multi-submit crash latch 和 normalized ranked path 继续推进。

2026-07-04 追加更新：Task 01/02/03/06 第一版也已落地。Task 01 增加离线 sink/path/failure taxonomy evaluator；Task 02 将 `record_sink_candidate` 升级为 role-aware review protocol；Task 03 将 `ranked_vulnerability_paths` 扩展为带 crash-family role、event-pair、paired endpoint、false-positive guards 的 static stack surrogate；Task 06 已把这些静态信号投影到 `GLOB` / `GREP` / `READ`，并增加 bounded `static lead` annotation、next-hop 和对应 trace metrics。当前本地全量回归为 `208 passed`。下一步主线进入 Task 04 PoC recipe/sanity，再完成 Task 05 feedback replanning。

## 1. 从 CyberGym 设计和优秀方案得到的约束

CyberGym Level 1 的本质是：给 agent vulnerability description 和 pre-patch codebase，要求它生成一个 raw input PoC，从程序 entry point 准确走到漏洞点并触发 sanitizer crash。成功不是只定位一个函数，而是要让输入满足 harness、parser、dispatch、漏洞条件和 post-patch discriminant。

官方材料有几个对我们非常关键的信号：

1. **描述和代码是 Level 1 的核心输入**。Level 2 加入 crash stack trace 会显著提高成功率；这说明“从描述到真实 crash path 的定位”是主要瓶颈之一。
2. **长 PoC 是难点**。超过 100 bytes 的 ground-truth PoC 成功率很低，这说明 format carrier、seed mutation、字段约束比单点 sink 名称更重要。
3. **成功 trace 的模式是关键词定位 → 阅读相关文件 → 构造 testcase → mutation → crash**。这和我们 v13 的候选/submit loop 方向一致，但我们缺少足够强的 “source-backed PoC recipe”。
4. **效率也重要**。官方 submission guideline 要求报告 cost/time/LLM requests；v13 非成功任务 150+ steps、17+ submits，说明不仅要提高成功率，还要减少错误路径上的消耗。
5. **动态分析要明确声明且有泄漏风险**。用户已经明确不想引入动态分析，所以我们只能使用 source/static/corpus/submit oracle 反馈。

一些高分系统设计复盘强调 situational context、harness meta-context、模块化任务和 feedback iteration 的作用。它们用动态 instrumentation 获得额外收益，但这部分不适用于我们；可迁移的是：**agent 必须相信 harness 是可触发的、优先理解 harness 约束、把复杂探索结果浓缩为 PoC agent 能消费的 recipe，而不是让主模型在原始日志里游泳。**

### 1.1 对 Xuanwu Atuin / MopMonk / Crystalline 的可迁移理解

三个优秀方案给出的共同启发不是“某一个工具决定成败”，而是一个闭环：

```text
structured campaign state
  + vulnerability-specific SOP
  + accumulated domain/procedural memory
  + negative evidence preservation
  + concrete input construction recipe
  + feedback-driven replanning
```

对我们当前 Level 1 模式，应该这样取舍：

| 来源 | 可迁移点 | 不能直接迁移点 | 我们的落地方式 |
|---|---|---|---|
| Xuanwu Atuin | manager 维护 campaign state、evidence gaps、failed hypotheses、PoC-target mismatch；SOP 约束环境理解、target path、PoC iteration、final evidence quality | 其方案使用 Docker 动态测试/debugger 和多 agent；用户明确不引入动态分析，当前也不应扩大多 agent | 用 typed state + six-section brief 实现 manager state；用 Next Action 实现 workflow hook；用 feedback taxonomy 标记 target mismatch |
| MopMonk | vulnerability-oriented memory：goal/code-path/input-format/candidate-PoC/negative-evidence/verification-state/next-constraint | 公开仓库是设计报告，closed-source；多 agent shared memory 不是当前主路线 | 把六类 memory 投影到现有 state 字段；尤其补 `negative evidence` 和 `next constraint`，避免 no-trigger 后重走旧路 |
| Crystalline | preseed general format/sanitizer knowledge；Recall → Understand → Craft/Fuzz → Validate → Submit → Remember；procedural recipes 和 principles 跨任务迁移 | 其 memory layer closed-source；提到 fallback libfuzzer、validate，本轮不引入动态分析；不能使用 CyberGym task-specific memory 泄漏 | 建本地 general-purpose procedure library：format recipes、sanitizer principles、crash-family construction rules；只用非 CyberGym/抽象化知识，或从本次 runs 中沉淀为非 task-specific principles |

最重要的修正：**我们的目标不应该只是 sink recall，而是“每次尝试都带着可复用的 memory object 前进”。**

这意味着 vNext_2 需要把现有状态重命名/重排成一组 CyberGym Level 1 的核心对象：

```text
Vulnerability Goal
Code Path
Input Format
Candidate Sink / Path
PoC Recipe
Negative Evidence
Verification State
Next Constraint
```

当前实现里已经有这些对象的碎片：

- `description_analysis` ≈ Vulnerability Goal。
- `harness_resolution` / `input_format.consumption` ≈ Input Format。
- `ranked_vulnerability_paths` / `call_chain_nodes` ≈ Code Path。
- `sink_candidates` ≈ Candidate Sink。
- `call_chain_gates` / `active_input_mappings` ≈ Next Constraint 的一部分。
- `feedback_history` / `failure_history` / `hot_feedback_window` ≈ Verification State。
- `exploration_notes` / `reflection_history` ≈ raw memory，但缺少 typed negative evidence 和 procedure recall。

因此新增设计应尽量**重组这些字段，而不是新增平行记忆系统**。

另外，要把静态分析能力投影到模型最常使用的工具界面。`ranked_vulnerability_paths` 如果只出现在 context 里，模型仍可能在后续 `GREP` / `READ` 中走散；下一版应让 `GLOB` / `GREP` / `READ` 返回 static-aware 排序、role annotation 和 next-hop，把它们变成通向 sink/path 的交通系统。

### 1.2 Level 1 模式下的核心差异

很多优秀方案可以利用 crash stack、local crash observation、debugger 或 cross-task memory。我们的任务模式固定是：

```text
description.txt + repo  =>  poc
```

所以必须严格区分三类信息：

1. **Runtime allowed**
   - vulnerability description
   - pre-patch source repo
   - submit harness / corpus / sample files
   - tool observations from reading/searching/writing
   - `submit_poc` oracle result

2. **Offline-only**
   - `error.txt`
   - ground-truth crash stack
   - v12/v13 trace comparisons
   - sink hit-rate evaluator
   - rule tuning and regression labels

3. **General prior allowed**
   - sanitizer class knowledge
   - file format construction principles
   - general vulnerability exploitation procedures
   - abstract principles learned from completed runs, as long as they do not contain task-specific identifiers, PoC bytes, or error-stack facts

这给出一个非常实际的架构约束：

> `error.txt` 可以训练/评估我们的 heuristic 和 prompt，但不能成为某个 task 的 runtime memory。  
> memory 可以存“TIFF IFD length fields are endian-gated”，不能存“arvo:12345 的 sink 是 FooBar at line 88”。

### 1.3 新增一个轻量 Procedure Memory，而不是大规模 RAG

从 MopMonk 和 Crystalline 看，跨任务可迁移的不是 raw trace，而是抽象 procedure/principle。我们可以在当前 repo 里实现一个很轻量、可审计的 procedure memory：

新增文件建议：

```text
agent_prompts/procedure_memory/
  bounds_overflow_recipe.md
  uninitialized_value_recipe.md
  lifetime_uaf_recipe.md
  integer_size_recipe.md
  segv_dispatch_recipe.md
  format_carrier_recipes.md
```

运行时选择逻辑：

- `crash_type` / `description_analysis.vuln_type` 决定最多加载 1–2 个 recipe。
- recipe 只能是 general guidance，不含 task id、具体 PoC、ground-truth stack。
- 渲染位置：
  - 不新增 observation section。
  - 在 phase prompt 或 bug guidance 中作为方法论。
  - 对当前 task 的具体字段仍必须来自 `Required Conditions`。

这等价于把 Crystalline 的 preseed/procedure 层做成一个可审计的、静态的、低风险版本。

### 1.4 必须补 typed Negative Evidence

MopMonk 反复强调 negative evidence：non-triggering attempts、unreachable paths、format errors 不能丢。v13 现在有 feedback/failure history，但没有足够清晰地投影到 path/gate/mapping/candidate。

新增 state 约定：

```python
negative_evidence = [
  {
    "evidence_id": "...",
    "kind": "no_crash_unknown|path_not_reached|path_reached_no_trigger|trigger_condition_not_satisfied|format_error|unreachable_path|wrong_crash|repeated_candidate|bad_seed",
    "candidate_id": "...",
    "ranked_path_id": "...",
    "mapping_id": "...",
    "family_id": "...",
    "summary": "...",
    "avoid_next": "...",
    "created_step": 42,
    "ttl": 8,
  }
]
```

初期可放在 `state.metadata["negative_evidence"]`，后续再 dataclass 化。

消费规则：

- `Current Assessment > Rejected` 显示最近 1–3 条高价值 negative evidence。
- `Experiments` 显示最近 submit 的 outcome 和它 refute/question 了哪个 candidate/gate/mapping。
- `Next Action` 若检测到同一 `avoid_next` 被重复违反，阻止继续盲目生成同类 PoC。

这比“记录失败了”更重要：它要告诉模型下一次**不要再做什么**。

## 2. 当前实现链路中真正该落点的位置

当前主链：

```text
CyberGymAdapter.from_task_dir(...)
  -> cli.build_agent(...)
  -> CyberGymAgent.init_state(...)
  -> build_system_prompt() + prepare()
  -> tools
  -> reduce()
```

现有关键实现：

- `agent.py`
  - `prepare()` 调 `_render_observation()`。
  - `reduce()` 消费 tool result，驱动 phase、candidate、feedback。
  - READ 后会抽 constraints；`record_sink_candidate` 后会触发 `_pending_sink_analysis`。

- `state.py`
  - 已有 `DescriptionAnalysis`、`VerifiedCodeRef`、`SinkCandidate`、`ranked_vulnerability_paths`、`active_input_mappings`、`call_chain_gates`。
  - 但 candidate role/path selection 大多在 `metadata`，还没有成为稳定协议。

- `analysis/service.py`
  - 已有 `_navigation_rows()`、`discover_sink_navigation_leads()`、`reachable_functions_from_entry()`、`discover_ranked_vulnerability_paths()`、`analyze_sink_candidate()`。
  - 当前问题不是没有入口，而是候选召回通道偏窄、role 区分弱、Top-K 和模型 review 的连接弱。

- `agent_impl/static_analysis_runtime.py`
  - 已经自动调用 `discover_ranked_vulnerability_paths()` 并 `_sync_ranked_paths()` 到 `state.ranked_vulnerability_paths` 和 `state.sink_candidates`。
  - 但 synced candidate 默认只是低置信 static_navigation，模型常常没有 review/record，导致 evaluator 看起来 candidate missing。

- `tracking_tools.py::RecordSinkCandidateTool`
  - 当前参数主要是 `function/evidence/location/confidence`。
  - 缺少 `candidate_role`、`ranked_path_id`、`source_span`、`paired_with`。
  - 这导致模型记录了一个函数，但系统不知道它是 crash site、causal site、path anchor，还是 dangerous primitive。

- `agent_impl/observations.py`
  - v13 observation 结构已很好：六段式 100% 合规。
  - 下一步不能破坏这个；新信息必须进入 `Current Assessment`、`Vulnerability Path`、`Required Conditions`、`Experiments`、`Next Action`。

所以技术落点不是新增一个大模块，而是沿现有链路插四个“窄而硬”的 contract：

```text
Static candidate pool
  -> role-aware reviewed sink candidate
  -> PoC recipe / static input mapping
  -> feedback-driven candidate/gate/mapping replanning
```

## 3. 第一优先级：把 sink 从“一个函数名”升级为“静态栈替代物”

### 为什么

CyberGym Level 2 的 crash stack trace 会显著提升成功率。我们不能把 `error.txt` 给 runtime，但可以让静态分析产出一个类似 stack trace 的东西：

```text
harness entry
  -> parser / dispatcher
  -> path anchor
  -> crash_site / causal_site / dangerous primitive
```

这就是 `discover_ranked_vulnerability_paths()` 应该真正成为的东西：**static stack surrogate**。

### 具体怎么改

#### 3.1 改 `analysis/models.py`

把 ranked path 的输出字段稳定下来：

```python
RankedVulnerabilityPath:
    path_id: str
    endpoint_role: str  # crash_site | causal_site | path_anchor | dangerous_primitive
    candidate_family: str  # bounds | uninit | lifetime | integer | segv | overlap | ...
    generation_channels: list[str]
    score_breakdown: dict[str, float]
    chain: list[dict]  # entry -> ... -> endpoint
    endpoint: dict
    paired_endpoint: dict | None
    required_pair_role: str
    next_read: dict
    false_positive_guards: list[str]
```

现有 dict 结构可以兼容，只要新增字段默认空。

#### 3.2 改 `analysis/vulnerability_knowledge.py`

从“按 crash type 给 name hints”升级为“按 crash family 给 endpoint role schema”。

需要实现：

```python
def crash_family(crash_type: str) -> str

def endpoint_role_schema(crash_type: str) -> list[RoleSchema]

def score_endpoint_for_role(signal, role_schema) -> float

def required_event_pairs(crash_type: str) -> list[tuple[str, str]]
```

角色规则：

- bounds:
  - `crash_site`: array/pointer/memory access where index/len/offset is input controlled
  - `dangerous_primitive`: memcpy/memmove/string/typed-read/write
  - `path_anchor`: parser/decoder caller

- uninit:
  - `causal_site`: missing initialization / partial producer / short-input error path
  - `crash_site`: branch/compare/copy/hash/serialize consuming value
  - 必须支持 pair，不要只输出一个 `check*` 函数。

- lifetime:
  - `causal_site`: free/delete/realloc/unref/release/erase/clear/destructor
  - `crash_site`: later deref/member/copy/compare/callback
  - 输出 invalidation/use pair。

- SEGV:
  - competing hypotheses：null, corrupted pointer/index, bad cast, function pointer。
  - 不要所有 SEGV 都按 null。

#### 3.3 改 `analysis/indexer.py`

补 structural risk signals，重点不是函数名，而是 AST 结构：

- `array_access`
- `pointer_arithmetic`
- `pointer_deref`
- `typed_read`
- `typed_write`
- `memory_copy`
- `branch_on_value`
- `partial_initialization`
- `out_param_write`
- `lifecycle_invalidation`
- `indirect_call`
- `virtual_call`
- `bad_cast_or_tag_dispatch`

每个 signal 必须带：

```python
kind
expression
location
severity
parameter_dependencies
reason
```

其中 `parameter_dependencies` 是后续判断 input-control 的关键。

#### 3.4 改 `analysis/service.py::discover_ranked_vulnerability_paths`

现在它从 `_navigation_rows()` 取前 50，再对 endpoint 做 path 和 score。下一版应改成多通道召回：

```text
Channel A: description-local downstream
  verified description ref 是 caller/path anchor 时，向 callees/downstream risk 扩 3-5 hop

Channel B: entry-forward
  从 selected harness entry BFS，fast depth 8，不够再 depth 24

Channel C: risk-backward
  从 crash-family-compatible risk endpoints 反向找 callers，与 entry frontier meet

Channel D: structural hazard
  array/pointer/typed read/lifecycle/out-param patterns，不依赖危险 API 名称

Channel E: event-pair completion
  UAF/uninit/integer/overlap 需要补 paired endpoint

Channel F: C++ dispatch expansion
  qualified methods/operator/template/virtual/function pointer 保留多候选
```

内部保留 pool 50–80 个，最终 diversify 成模型可见 Top-5：

- 每个 file 最多 2 个。
- 每个 role 最多 3 个。
- 对 event-pair，允许一对占两个 slot。
- Top-K 必须尽量包含至少一个 `crash_site`，否则设置 `candidate_set_incomplete_reason`。

输出不是“函数菜单”，而是：

```text
Path #1 role=crash_site family=bounds score=...
entry -> parser -> endpoint
why: array_access, len controls index, reachable depth=...
next_read: file:line
```

### 预期效果

这一步直接针对 v13 的 `CrashPathRecall@5=59.18%`。合理目标是先到 65–70%。  
它不保证 crash rate 立刻上去，但会减少模型读错区域和错误 submit 的概率。

## 4. 第二优先级：让模型必须 review Top-K，而不是自己凭感觉记一个 sink

### 为什么

v13 的 static candidates 其实很多时候已经在 state 里，但 evaluator 看的是 `record_sink_candidate`。如果模型没有把 static candidate review 成 model-reviewed candidate，那么：

- trace 中 candidate 看起来 missing；
- active sink 不稳定；
- feedback 无法知道该 rotate 哪个候选；
- prompt 仍然在泛泛要求“找 sink”。

所以要建立一个 candidate protocol：

```text
static candidate -> model reads endpoint -> record_sink_candidate(role, path_id) -> active sink
```

### 具体怎么改

#### 4.1 改 `tracking_tools.py::RecordSinkCandidateTool`

参数增加：

```python
"candidate_role": {
    "type": "string",
    "description": "crash_site | causal_site | path_anchor | dangerous_primitive | unknown"
},
"ranked_path_id": {
    "type": "string",
    "description": "Path id from Vulnerability Path if this candidate reviews a static candidate"
},
"source_span": {
    "type": "object",
    "description": "Optional {file,line,end_line}"
},
"paired_with": {
    "type": "string",
    "description": "Optional paired candidate/path for UAF/uninit/integer/overlap"
}
```

执行逻辑：

- 若 `ranked_path_id` 匹配 static candidate：
  - 保留 static metadata。
  - 设置 `reviewed=True`。
  - 设置 `selection_status=active`。
  - 将 `candidate_role` 写入 metadata。

- 若 `candidate_role=path_anchor`：
  - 允许记录，但不要当最终 crash sink。
  - `Next Action` 必须继续要求 downstream leaf/paired endpoint。

- 若 `candidate_role=crash_site` 或 `causal_site`：
  - 可触发 `_pending_sink_analysis`。
  - 进入 active sink selection。

#### 4.2 改 `state.py`

不用大迁移，先把 `SinkCandidate.metadata` 标准化：

```python
metadata = {
    "candidate_role": "...",
    "ranked_path_id": "...",
    "generation_channels": [...],
    "score_breakdown": {...},
    "source_span": {...},
    "paired_with": "...",
    "selection_status": "unreviewed|active|rejected|cooldown",
    "reviewed": bool,
}
```

然后修改 `_primary_sink_id()`：

优先级：

1. reviewed crash_site
2. reviewed causal_site with pair
3. reviewed dangerous_primitive
4. reviewed path_anchor
5. unreviewed static_navigation

连续 no-trigger 后，降低当前 candidate priority，而不是一直锁死 active sink。

#### 4.3 改 `agent_impl/observations.py`

- `Current Assessment`：
  - confirmed/reviewed candidate 才算 Confirmed。
  - static_navigation 放 Likely。
  - 每个 candidate 显示 role。

- `Vulnerability Path`：
  - Top-K path 显示 path_id、role、next_read。
  - 不要重复完整 score dict。

- `Next Action`：
  - 如果没有 reviewed sink，推荐：

```text
READ(path="...", offset=..., limit=...)
Stop condition: confirm/reject path_id=vpath_x as crash_site/causal_site/path_anchor,
then call record_sink_candidate(..., candidate_role=..., ranked_path_id=...)
```

#### 4.4 改 prompt

文件：

- `agent_prompts/phase/exploration.md`
- `agent_prompts/phase/investigation.md`
- `agent_prompts/system/runtime_context_protocol.md`

核心文案原则：

- analysis candidates are leads, not facts。
- If candidate role is path_anchor, inspect downstream leaf before PoC。
- For UAF/uninit, one endpoint is partial; find the paired endpoint if visible。
- Do not spend broad searches while unreviewed Top-K paths exist。

### 预期效果

这一步优先修：

- candidate missing
- first candidate step
- active sink selection failure

目标：

- with_candidates 接近 100%。
- first candidate 从 v13 8.4 拉回 <=7.5。
- GT in Top-K but not active 的失败减少。

## 5. 第三优先级：把静态分析投影到经典工具 GLOB / GREP / READ

### 为什么

`ranked_vulnerability_paths` 和 `static_navigation` 如果只显示在六段式 context 里，模型后续仍可能在普通 `GREP` / `READ` 中走散。v13 失败里很多不是“完全没有线索”，而是模型没有沿着正确文件、函数、caller/callee 继续读，或者把 path anchor 当成 crash site。

所以经典工具增强的目标不是新建一个复杂专家工具，而是让模型最常用的工具具备 static-aware 排序、role annotation 和 next-hop。

```text
GLOB/GREP/READ raw result
  + ranked path relation
  + candidate role
  + nearby risk signal
  + next READ / record action
  => faster sink review and fewer candidate misses
```

### 具体怎么改

#### 5.1 新增 `agent_impl/static_tool_hints.py`

新增轻量 annotation 层：

```python
@dataclass
class ToolHint:
    role: str  # entry | parser_gate | dispatch | path_anchor | crash_site | causal_site | wrapper | seed | unknown
    confidence: float
    reasons: list[str]
    next_actions: list[str]
    path_id: str | None = None
    candidate_id: str | None = None
    family: str | None = None
```

核心函数：

```python
annotate_file_path(state, path)
annotate_text_hit(state, path, line, text)
annotate_read_region(state, path, start, end, content)
rank_annotated_hits(hits)
```

输入信号来自：

- `description_analysis`
- `ranked_vulnerability_paths`
- `sink_candidates`
- `call_chain_nodes`
- `call_chain_gates`
- `active_input_mappings`
- crash-family pattern hints

#### 5.2 改 `GLOB`

修改：

```text
agent_impl/tools.py::GLOB
agent_impl/tool_render.py::render_GLOB
agent_impl/repo_index.py
```

让 GLOB 优先展示：

- harness / fuzz entry 文件；
- ranked path 上的文件；
- description term 命中的文件；
- parser / decoder / reader / import / load 命名文件；
- seed / corpus 相关文件；
- 包含 family-specific risk term 的文件。

输出示例：

```text
repo-vul/src/lib/pngread.c
  [role=parser_gate rank=high] matches "chunk"; on ranked path p3
  next: GREP("length|offset|crc|chunk", path="repo-vul/src/lib/pngread.c")
```

#### 5.3 改 `GREP`

修改：

```text
agent_impl/tools.py::GREP
agent_impl/tool_render.py::render_GREP
```

对命中做 role annotation：

```text
match #7 src/foo/parser.c:381 parse_record(...)
  [role=parser_gate score=0.82] caller chain reaches candidate sink; validates record length
  next: READ(match_id="grep_7", radius=80)
```

排序信号：

- 是否位于 ranked path chain；
- 是否是 static_navigation candidate；
- 是否附近有 family-specific risky pattern；
- 是否匹配 description 中的 function/file/format term；
- 是否只是 wrapper。

#### 5.4 改 `READ`

修改：

```text
agent_impl/tools.py::READ
agent_impl/tool_render.py::render_READ
```

READ 内容不变，但增加最多 6 行 `Static context`：

```text
Static context:
- enclosing function: parse_record
- role: parser_gate on path p3 -> candidate c2
- nearby risk: memcpy(dst, src, len) at line 421
- controlling field: rec->len parsed at line 377
- next: READ caller parse_file; READ struct Record; record_gate if confirmed
```

规则：

- wrapper/path_anchor -> 建议 follow callee，不要马上当 final sink。
- parser_gate -> 建议 record gate 或追踪 input field。
- crash_site/causal_site -> 若 source evidence 匹配 bug family，建议 `record_sink_candidate`。
- annotation 是 lead，不是 fact；durable 信息仍要通过 tracking tools 入 state。

### 预期效果

- `first_useful_read_step` 下降。
- `first_candidate_step` 下降。
- `candidate_set_miss` 下降。
- `CrashPathRecall@5` 提升。
- 模型更多使用 `READ(match_id=...)` 和 next-hop，而不是重复 broad GREP。

## 6. 第四优先级：从 sink/path 生成 PoC Recipe，并增加通用 PoC sanity toolbox

### 为什么

sink 准了不等于 PoC 成功。PoC 成功需要一个 recipe：

```text
carrier/seed
  + magic/format gates
  + dispatch selector
  + length/index/offset/lifecycle trigger
  + mutation target
```

v13 现在有 `call_chain_gates`、`suggested_constraints`、`active_input_mappings`，但它们更像条件列表，不像“下一步该怎么改输入”的 recipe。

安全专家给出的 `bmz-q-q/cybergym_agent` `para_action` 分支和 `agentic-poc/toolbox/font.py` 类建议也应该放在这一层：PoC 不是只要“写一些危险 bytes”，还要把构造动作模板化，并尽量保持 carrier 能通过前置 parser。下一版需要把 seed 选择、字段定位、局部 mutation、sanity check、submit 变成可审计动作链，同时把 magic、hex/offset、简单字段、corpus seed delta、SFNT/OTF/CFF2 这类通用结构检查做成 pre-submit sanity toolbox。它不运行目标程序、不读 `error.txt`，只判断“这个 PoC 是否明显连格式入口都过不了”。

### 具体怎么改

#### 5.1 新增/标准化 PoC recipe shape

可以放在 `state.metadata["poc_recipe"]` 起步，后续再 dataclass 化：

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
      "argument_role": "length|index|offset|pointer|state|selector",
      "source_kind": "direct_offset|struct_field|symbolic|seed_relative",
      "offset": 16,
      "width": 4,
      "endianness": "little",
      "value_strategy": "oversize|negative|wrap|short_chunk|duplicate_free_sequence",
      "evidence": "file:line ..."
    }
  ],
  "open_gaps": [...]
}
```

#### 5.2 新增通用 PoC sanity toolbox

新增：

```text
agent_impl/poc_sanity.py
agent_impl/poc_sanity_formats.py
```

核心接口：

```python
def inspect_poc_bytes(path: str, *, expected_format: str = "", seed_path: str | None = None) -> PoCSanityResult:
    ...
```

检查范围：

- 通用：empty/min-size、magic/header、declared offset/width/length in-bounds、mutation target 是否真的被修改。
- corpus：seed 与 PoC outer container 是否保持一致、delta offset/length、是否破坏 header/table/chunk skeleton。
- format：PNG/JPEG/PDF/ZIP/WAV/BMP 轻量 carrier check。
- font：SFNT magic、`numTables/searchRange/entrySelector/rangeShift`、table directory offset/length、OTF `CFF ` / `CFF2` table、CFF/CFF2 header/offSize 基本 sanity。

接入：

- `agent_impl/feedback.py::_pre_submit_validate()` 在 submit 前调用。
- `agent_impl/tools.py` 可新增 `PoCSanityCheck(path, expected_format="", seed_path="")` 供模型主动检查。
- `agent_impl/observations.py` 把结果压入 `Required Conditions` / `Experiments` / `Next Action`，不新增 section。
- `fail` 只阻塞明显 carrier-invalid PoC；`warn` 不阻塞 submit，避免误伤有意畸形输入。
- `agent_prompts/procedure_memory/poc_action_templates.md` 写入 para_action 风格的通用动作链：seed_select -> locate_field -> mutate_local_bytes -> sanity_check -> submit。

这一步可以迁移 `agentic-poc/toolbox` 的通用 magic/hex/simple-field/corpus/font 逻辑；如果参考仓库不可访问，则按相同接口重写，不阻塞 vNext。

#### 5.3 改 `analysis/input_mapping.py`

现有 mapping 要统一成 PoC-oriented fields：

- `argument_role`
- `source_kind`
- `offset`
- `width`
- `endianness`
- `value_strategy`
- `evidence`
- `status`

#### 5.4 改 `agent_impl/constraint_sinks.py`

按 crash family 选 critical arguments：

- bounds：length/index/offset/capacity/stride。
- uninit：producer output、consumer branch/copy/compare。
- UAF：object pointer、owner/refcount、invalidate/use sequence。
- integer：arithmetic expression + consumer。
- overlap：src/dst/len ranges。

别再平铺所有参数；只给能转 PoC 的参数。

#### 5.5 改 `agent_impl/static_analysis_runtime.py::_populate_constraints_from_brief`

从 `analyze_sink_candidate()` 的 brief 中同步：

- confirmed/inferred mappings -> `active_input_mappings`
- recipe -> `metadata["poc_recipe"]`
- pair gaps -> `open_analysis_unresolved_ids`
- required gates -> `call_chain_gates` / `suggested_constraints`

#### 5.6 改 `agent_impl/observations.py::_render_required_conditions`

展示顺序改成：

1. Concrete mutation targets。
2. Carrier / seed strategy。
3. PoC sanity / carrier checks。
4. Format/dispatch requirements。
5. Trigger conditions。
6. Open gaps。
7. Refuted conditions。

示例：

```text
## Required Conditions
- ✓ Carrier: mutate corpus seed `tests/foo.mng`; keeps MNG signature [source: analysis service]
- ✓ Dispatch: create `mng_LOOP` chunk [source: code reading]
- ? Trigger: chunk length < 5; field likely at seed-relative offset ... [source: analysis service]
- Next mutation: set LOOP length to 0x00000004 and preserve CRC if parser checks it.
```

这比“有一个 bounds_gate”更能驱动模型写 PoC。

#### 5.7 改 `agent_prompts/phase/formulation.md`

把 formulation prompt 从“多研究一点”变成 recipe executor：

- If recipe has seed_path, mutate seed first。
- If concrete offset exists, write exact bytes。
- After writing binary/corpus-mutated PoC, run `PoCSanityCheck` or equivalent FileInfo/HexView/StructProbe checks before submit。
- For font/SFNT/OTF/CFF2, preserve table directory and verify target table in-bounds before payload mutation。
- If offset unknown but carrier known, preserve seed and mutate smallest local region。
- If recipe is symbolic, generate 2–3 variants max, then submit。
- Do not abandon source-backed recipe after one no-trigger; revise one condition。

### 预期效果

修的是：

- active sink near GT but no-trigger。
- long PoC format failures。
- repeated blind submit。

目标：

- non-success completed avg steps 从 150.9 降到 <=120。
- avg submits 下降但 success 不下降。
- completed crash rate 推到 80%+。

## 7. 第五优先级：submit feedback 只做 replanning，不做盲目喷射

### 为什么

v13 成功来自 submit-feedback loop，但失败任务里平均 17 次 submit 太高。说明模型在错路径上很能坚持——这既是优点也是浪费。

### 具体怎么改

#### 6.1 改 `agent_impl/feedback.py`

把 feedback 归一成：

```python
feedback_effect = {
    "outcome": "success|no_crash_unknown|path_not_reached|path_reached_no_trigger|wrong_crash|too_broad|submit_error",
    "likely_failure_layer": "carrier|dispatch|path_gate|trigger_condition|sink_selection|unknown",
    "recommended_revision": "revise_mapping|rotate_candidate|find_paired_endpoint|submit_ready|cooldown_family",
    "affected_candidate_id": "...",
    "affected_gate_id": "...",
    "affected_mapping_id": "..."
}
```

无动态分析时，`likely_failure_layer` 只能是 conservative inference：

- output 明确格式错误/parse error -> carrier/format。
- 没 crash + no sanitizer -> `no_crash_unknown`；不得无证据写成 `path_not_reached`。
- 连续同 family no-trigger + no mapping revision -> rotate candidate。
- wrong sanitizer/class -> too_broad or wrong root。

#### 6.2 改 `family_runtime.py` / `agent_impl/candidates.py`

family budget：

- 新 family 前 2 次 submit 免费。
- 有 partial signal 扩展到 5。
- 连续 3 次 no-trigger 且 recipe 未变化 -> cooldown。
- Top-K 还有 unreviewed crash_site -> rotate。
- UAF/uninit 缺 pair -> 回 investigation 找 pair，不继续喷变体。

#### 6.3 改 `submit_queue.py`

不要阻塞第一次 submit；但阻塞重复低信息 submit：

```text
same family + same mutation axis + same no-trigger signature >= 3
  -> require replan: mapping revision or candidate rotation
```

#### 6.4 改 `agent_impl/observations.py::_render_experiments`

Experiments 不只列结果，还列对计划的影响：

```text
Attempt 3: no_crash_unknown
Impact: active path is not refuted; trigger length mapping is questioned.
Next: revise LOOP length field or rotate to candidate #2 if no new mapping.
```

#### 6.5 改 `agent_impl/observations.py::_render_next_action`

优先级：

1. ready PoC -> submit。
2. success -> stop/final。
3. repeated no-trigger with unchanged recipe -> replan。
4. candidate rotation recommended -> review next Top-K。
5. unresolved concrete mapping -> READ/trace statically。
6. no reviewed sink -> review Top-K。
7. otherwise -> write candidate。

### 预期效果

- 减少 timeout submitted-no-trigger。
- 降低非成功任务 step/submits。
- 防止 completed crash rate 靠留下 running 假性提高。

## 8. 实施顺序：不要从最大静态分析开始

建议按这个顺序做，风险最低：

### Step A-1 — Runtime Correctness Hotfixes（先修事实层）

改：

- `submit_tool.py`
- `agent.py`
- `agent_impl/feedback.py`
- `agent_impl/observations.py`
- `analysis/service.py`
- `analysis/models.py`
- `agent_impl/static_analysis_runtime.py`
- `tests/test_submit_parallel_feedback.py`
- `tests/test_feedback_taxonomy_no_crash.py`
- `tests/test_ranked_path_normalization.py`

目标：

- 同轮多个 `submit_poc` 中任一 PoC crash 后，runtime context 不被后续 no-crash 覆盖。
- `NO_TRIGGER` / `vul_exit_code=0` 默认是 `no_crash_unknown`，不会直接 refute path gates。
- `path_not_reached` 只有在有明确 carrier/dispatch/path evidence 时才使用。
- ranked path 进入 state 前做 direction/dedupe normalize；方向错误或重复节点无法修复时降级为 partial。

原因：如果事实层错了，后续 negative evidence、candidate rotation、PoC recipe 都会优化到错误方向。

### Step A0 — Procedure Memory + Negative Evidence（低风险地吸收优秀方案经验）

改：

- `state.py`
- `agent_impl/feedback.py`
- `agent_impl/observations.py`
- `agent_impl/prompts.py`
- `agent_prompts/bug_guidance/*.md`
- 新增 `agent_prompts/procedure_memory/*.md`
- `tests/test_vnext_context_rendering.py`

目标：

- 把优秀方案里的 campaign memory / SOP / negative evidence 落到当前单 agent state。
- 不新增动态分析。
- 不使用 task-specific ground truth。

最小实现：

1. `feedback.py` 在 no-trigger / wrong crash / format failure 后写入 `state.metadata["negative_evidence"]`。
2. `observations.py` 把 recent negative evidence 投影到：
   - `Current Assessment > Rejected`
   - `Experiments`
   - `Next Action`
3. `prompts.py` 根据 crash family 加载最多一个 procedure memory snippet。
4. tests 保证 procedure memory 不产生新 `##` section，不含 task-specific identifiers。

这一步不直接提升 sink recall，但会降低 v13 非成功任务的重复错误，是后续 Candidate Protocol、Static-aware Tools 和 PoC Recipe 的地基。

### Step A — Candidate Protocol v2（最快 sink 指标收益）

改：

- `tracking_tools.py`
- `state.py`
- `agent_impl/observations.py`
- `agent_prompts/phase/exploration.md`
- `scripts/evaluate_trace_sink_hit_rate.py`

目标：

- static Top-K -> model reviewed candidate 这条链打通。
- evaluator 能区分 static candidate、reviewed candidate、active candidate。
- candidate missing 接近 0。

原因：这不需要复杂 analyzer 改动，却能立刻修 v13 的明显指标缺口。

### Step B — Static Stack Surrogate v2

改：

- `analysis/vulnerability_knowledge.py`
- `analysis/vuln_patterns.py`
- `analysis/indexer.py`
- `analysis/service.py`
- `analysis/models.py`
- `agent_impl/static_analysis_runtime.py`

目标：

- CrashPathRecall@5 提升。
- Top-K 有 role diversity。
- UAF/uninit 有 event pair。

### Step B2 — Static-aware Classic Tools

改：

- `agent_impl/static_tool_hints.py`
- `agent_impl/tools.py::GLOB`
- `agent_impl/tools.py::GREP`
- `agent_impl/tools.py::READ`
- `agent_impl/tool_render.py`
- `agent_impl/repo_index.py`
- `agent_prompts/system/tool_usage.md`
- `agent_prompts/phase/exploration.md`
- `agent_prompts/phase/investigation.md`

目标：

- 把 Static Stack Surrogate 的结果投影到经典工具输出。
- GLOB/GREP/READ 给出 role annotation 和 next-hop。
- 模型更快从 search hit 转向 source-backed candidate。

### Step C — PoC Recipe + Sanity Toolbox v1

改：

- `analysis/input_mapping.py`
- `agent_impl/constraint_sinks.py`
- `agent_impl/constraint_dataflow.py`
- `agent_impl/static_analysis_runtime.py`
- `agent_impl/ir_renderer.py`
- `agent_impl/observations.py`
- `agent_impl/poc_sanity.py`
- `agent_impl/poc_sanity_formats.py`
- `agent_impl/tools.py::PoCSanityCheck`
- `agent_prompts/phase/formulation.md`

目标：

- Required Conditions 变成 mutation recipe。
- binary/font 类 PoC submit 前有 magic/结构/seed-delta sanity result。
- corpus-first 更稳定。
- no-trigger 后能定位是 mapping 还是 sink。

### Step D — Feedback Replanning v1

改：

- `agent_impl/feedback.py`
- `family_runtime.py`
- `submit_queue.py`
- `agent_impl/candidates.py`
- `agent_impl/validation.py`
- `agent_impl/observations.py`
- `agent_prompts/phase/verification.md`

目标：

- 降低非成功任务 submit/step。
- 提高 completed crash rate。

## 9. 最小可用版本定义

不要等所有东西都完美。一个值得跑 v14 smoke 的版本应满足：

1. observation 六段式仍 100%。
2. multi-submit crash signal 不会被同轮 no-crash 覆盖。
3. `vul_exit_code=0` 默认是 `no_crash_unknown`，不是 `path_not_reached`。
4. `Vulnerability Path` 不展示未标记的方向反转/重复节点完整 path。
5. `record_sink_candidate` 支持 role/path_id，并能被 trace evaluator 抽取。
6. `Vulnerability Path` 展示 Top-K static stack surrogate。
7. `Next Action` 能具体指向 Top-K path 的 READ/review，而不是泛泛找 sink。
8. `GLOB` / `GREP` / `READ` 至少有一个工具能展示 static-aware role annotation / next-hop。
9. `Required Conditions` 至少能展示一个 PoC recipe block，即使部分字段 symbolic。
10. feedback 能在连续 no-crash 后要求 mapping revision 或 candidate rotation。

## 10. 评价方式

每次 smoke / batch 都必须同时看：

- `crash/completed`
- `crash/all_started`
- `ExactSinkRecall@1/3/5`
- `CrashPathRecall@5`
- `CausalCoverage@5`
- `GT in Top-K but not active`
- `active near GT but no-trigger`
- `no_crash_unknown_rate`
- `path_not_reached_without_evidence_count`
- `path_normalization_warning_rate`
- `candidate missing`
- `first candidate step`
- `first useful read step`
- `GT in static-aware GREP/READ topK`
- `first submit step`
- `avg submits success/non-success`
- `non-success avg steps`
- observation 六段式 audit

如果出现：

```text
sink hit ↑, crash rate 不变
```

说明瓶颈在 Task C/D：PoC recipe 或 feedback replanning。

如果出现：

```text
crash rate ↑, sink hit 不变
```

说明 submit loop/context 变强，但定位仍有天花板，继续做 Static Stack Surrogate。

如果出现：

```text
completed crash rate ↑, crash/all_started 不变或下降
```

说明可能只是让困难任务 running，更不能算成功。

## 11. 一句话技术路线

下一版应该这样落：

```text
把 ranked_vulnerability_paths 做成 Level-2 stack trace 的静态替代品；
把 record_sink_candidate 做成 role-aware review 协议；
把 GLOB/GREP/READ 做成 static-aware 导航工具；
把 Required Conditions 做成 PoC mutation recipe + sanity checklist；
把 submit feedback 做成 candidate/gate/mapping replanning；
全过程保持六段式 context，不新增动态分析，不泄漏 error.txt。
```
