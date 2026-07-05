# Task 06 — Static-aware Classic Tools

## 目标

增强模型最常使用的经典工具：`GLOB`、`GREP`、`READ`，让它们从“原始文件/文本工具”升级为“静态分析感知的导航工具”。

本任务不新增动态分析，不改变工具的基础语义，不隐藏原始结果。核心是给经典工具结果增加：

- role-aware 排序；
- source-backed annotation；
- next-hop 建议；
- 与 `ranked_vulnerability_paths` / `static_navigation` / active candidate 的关系。

目标不是让工具输出变长，而是让模型每次搜索/阅读后更快回答三个问题：

1. 这个命中是 entry、parser gate、path anchor、crash site，还是 wrapper？
2. 下一步应该读 caller、callee、struct definition、field parser，还是 record candidate？
3. 这个结果和当前 sink/path 目标有什么关系？

## 当前证据

v13 的主要瓶颈之一是 `candidate_set_miss`。很多 trace 不是没有工具调用，而是工具调用后的路径选择不稳定：

- `record_sink_candidate` 覆盖率已经较高，但非成功 completed 的 `CrashPath@5` 只有 47.4%。
- `Vulnerability Path` 有时退化为单节点，说明模型没有沿着 entry → parser → sink chain 继续读。
- `arvo:10013` 这类任务在错误 active sink 上提交很多次，说明早期工具结果没有帮助模型区分 path anchor 和 true crash site。
- 当前已有 `FindSymbols`、`CallsiteSearch`、`RepoMap`、`ranked_vulnerability_paths`、`static_navigation`，但这些静态线索没有充分投影到最常用的 `GREP` / `READ` / `GLOB` 输出里。

因此本任务的定位是：把 Task 03 的静态分析收益送到模型实际使用最多的工具界面上。

## 具体代码修改

### 1. 新增 static-aware annotation 层

新增文件：

```text
agent_impl/static_tool_hints.py
```

核心数据结构：

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

@dataclass
class AnnotatedHit:
    path: str
    line: int | None
    text: str
    score: float
    hints: list[ToolHint]
```

新增函数：

```python
def annotate_file_path(state: CyberGymState, path: str) -> list[ToolHint]: ...
def annotate_text_hit(state: CyberGymState, path: str, line: int, text: str) -> list[ToolHint]: ...
def annotate_read_region(state: CyberGymState, path: str, start: int, end: int, content: str) -> list[ToolHint]: ...
def rank_annotated_hits(hits: list[AnnotatedHit]) -> list[AnnotatedHit]: ...
```

输入信号：

- `state.description_analysis`
- `state.ranked_vulnerability_paths`
- `state.sink_candidates`
- `state.call_chain_nodes`
- `state.call_chain_gates`
- `state.active_input_mappings`
- `state.metadata["candidate_set_incomplete_reason"]`
- `analysis/vuln_patterns.py` 的 crash-family keywords

### 2. 修改 `agent_impl/tools.py::GLOB`

当前 `GLOB` 返回匹配路径。新增 static-aware 排序和短 annotation：

```text
repo-vul/src/lib/pngread.c
  [rank=high role=parser_gate] matches description term "chunk"; on ranked path p3
  next: GREP("length|offset|crc|chunk", path="repo-vul/src/lib/pngread.c")
```

排序优先级：

1. harness / fuzz entry 文件；
2. `ranked_vulnerability_paths` 上的文件；
3. description term 命中的文件；
4. parser/decoder/reader/import/load 命名文件；
5. 包含 sink-like risk signal 的文件；
6. corpus/seed 相关路径。

要求：

- 不删除低分路径，只调整前 N 个展示顺序。
- 原始 count 保留。
- annotation 最多 2 行，避免污染 context。

### 3. 修改 `agent_impl/tools.py::GREP`

在 GREP 命中后做 role annotation 和重排。

排序信号：

- 命中是否位于 `ranked_vulnerability_paths` chain 上；
- 命中附近函数是否是 `static_navigation` candidate；
- 命中附近是否有 family-specific risky pattern：
  - bounds：`memcpy` / `memmove` / `strcpy` / `operator[]` / pointer arithmetic / loop bound；
  - integer：arithmetic expression feeding allocation/copy/loop；
  - uninit：out-param、conditional assignment、error path、later compare/copy/use；
  - lifetime：`free` / `release` / `unref` / `erase` / `clear` 后 later deref；
  - parser：length/checksum/chunk/table/record/field/offset；
  - dispatch：function pointer table、virtual dispatch、switch tag、format selector。
- 是否匹配 description 中的 function/file/format terms。
- 是否看起来只是 wrapper。

输出示例：

```text
match #7 src/foo/parser.c:381 parse_record(...)
  [role=parser_gate score=0.82] caller chain reaches candidate sink; validates record length
  next: READ(match_id="grep_7", radius=80)
```

### 4. 修改 `agent_impl/tools.py::READ`

`READ` 的原始文件内容不变，但在 header 或 footer 增加 `Static context`，最多 6 行：

```text
Static context:
- enclosing function: parse_record
- role: parser_gate on path p3 -> candidate c2
- nearby risk: memcpy(dst, src, len) at line 421
- controlling field: rec->len parsed at line 377
- next: READ caller parse_file; READ struct Record; record_gate if confirmed
```

增强点：

- 识别 enclosing function / method。
- 如果 READ 区域覆盖 ranked path node，展示 `path_id`、role、next node。
- 如果 READ 区域附近有 candidate sink，提醒是否 `record_sink_candidate`。
- 如果 READ 区域只是 wrapper，建议 follow callee。
- 如果 READ 区域是 parser gate，建议 record gate 或追踪 input field。

### 5. 修改 `agent_impl/tool_render.py`

新增渲染规则：

- annotation 用纯文本，不用 raw dict。
- 每条结果最多 2 行 hint。
- `READ` 的 `Static context` 必须在 bounded content 前后都可读，不能破坏已有 `has_more` / `READ(... offset=...)` 导航。
- 仍支持 `READ(match_id=...)`。

### 6. 修改 `agent_impl/repo_index.py`

扩展轻量索引字段：

```python
FileIndexRecord(
    path=...,
    symbols=[...],
    likely_roles=[...],
    parser_terms=[...],
    risk_terms=[...],
    format_terms=[...],
)
```

要求：

- 索引构建不能明显拖慢 task 启动。
- 只做 lexical / tree-sitter-lightweight 信号，不做完整 taint。
- 缓存到现有 repo index，不新增 runtime artifact 到源码仓库。

### 7. 修改 `analysis/service.py`

新增可选服务函数：

```python
def annotate_tool_hits(self, hits, *, state_snapshot, query_kind: str) -> list[AnnotatedHit]:
    ...
```

或先不走 service，直接在 `agent_impl/static_tool_hints.py` 读 state。第一版建议后者，减少跨层耦合；第二版再下沉到 analysis service。

### 8. 修改 prompt / context

文件：

```text
agent_prompts/system/tool_usage.md
agent_prompts/phase/exploration.md
agent_prompts/phase/investigation.md
agent_prompts/system/runtime_context_protocol.md
```

新增原则：

- Treat static-aware annotations as leads, not facts.
- Prefer `READ(match_id=...)` on high-role GREP hits before broad searching again.
- If `READ` says wrapper/path_anchor, follow next-hop before recording final sink.
- If `READ` says crash_site/causal_site and source evidence matches bug family, call `record_sink_candidate`.
- If `READ` says parser_gate, record gate or map input field before PoC.

annotation 不新增 observation section，只作为工具 observation 的短提示；真正 durable 信息仍要通过 `record_sink_candidate` / `record_gate` / `record_chain_node` 进入 state。

## 测试

新增：

```text
tests/test_static_tool_hints.py
tests/test_static_aware_grep_read_glob.py
```

覆盖：

- `GLOB` 对 harness/parser/ranked-path 文件排序靠前。
- `GREP` 命中 ranked path node 时显示 role/path_id/next READ。
- `READ` 对 wrapper 给出 follow-callee，不误标 crash_site。
- `READ` 对 crash-site 附近 risky op 给出 record_sink_candidate 建议。
- annotation 不包含 raw dict / XML / 新 observation heading。
- 所有经典工具仍能在无 state / 无 index 情况下退化为原始行为。

## 评价指标

离线 trace/eval 新增：

- `first_useful_read_step`
- `first_high_role_grep_hit_step`
- `GT in static-aware GREP topK`
- `GT in READ next-hop topK`
- `record_sink_candidate after high-role READ rate`

目标：

- `first_candidate_step` 下降。
- `candidate_set_miss` 下降。
- `CrashPathRecall@5` 提升。
- `non_success_avg_steps` 下降。

## 当前实现状态（2026-07-04）

第一版 static-aware classic tools 已落地，并通过本地全量回归：

```bash
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym python3 -m pytest tests -q
# 208 passed
```

已实现：

- 新增 `agent_impl/static_tool_hints.py`
  - 定义 `ToolHint` / `AnnotatedHit`。
  - 实现 `annotate_file_path()`、`annotate_text_hit()`、`annotate_read_region()`、`rank_annotated_hits()`。
  - 消费现有 `ranked_vulnerability_paths`、reviewed/unreviewed sink candidates、harness resolution、call-chain nodes、description terms 和 `repo_index_v2`。
  - annotation 明确使用 `static lead` 语义，不把启发式结果写成已确认事实。
- `GLOB`
  - 在完整匹配集合上先做 static-aware 排序，再应用 `max_results`。
  - harness、ranked endpoint、parser/dispatch/path files 会前置。
  - 不删除原始路径、size、kind 和 result count。
- `GREP`
  - 对返回的 content hits 做 role-aware 排序。
  - 每个命中最多显示一条 compact static lead，包含 role、score、path/candidate/family 和 next-hop。
  - `files_with_matches` 模式也支持 file-level hints。
  - 保留原始 match、preview、match_id 和 `READ(match_id=...)` 跳转。
- `READ`
  - 原始 bounded content、line numbers、has_more/continuation 不变。
  - 在正文前增加最多 5 条 `Static context (navigation leads; verify in source)`。
  - 能显示 enclosing function、ranked path role/path_id、candidate relation 和 wrapper/path-anchor downstream next-hop。
- `agent_impl/repo_index.py`
  - 每个 file record 增加 `likely_roles`、`parser_terms`、`risk_terms`、`format_terms`。
  - 只使用 bounded lexical / brace-depth structural signals，不做动态分析或完整 taint。
- prompt/context
  - `tool_usage.md`、exploration、investigation、runtime context protocol 已明确：
    - static annotation 只是 lead；
    - high-role GREP hit 优先 `READ(match_id=...)`；
    - wrapper/path_anchor 必须继续跟 next-hop；
    - durable 结论仍通过 candidate/chain/gate/mapping typed state 记录。
  - 同时移除 “no_trigger 必然等于 path_not_reached” 的旧提示。
- offline evaluator
  - 新增 `first_high_role_grep_hit_step`。
  - 新增 `first_static_aware_read_step`。
  - 新增 `high_role_grep_hit_count` / `static_aware_read_count`。
  - summary 新增 `candidate_after_static_read_count`。

测试：

- `tests/test_static_tool_hints.py`
  - entry/parser/crash-site 分类。
  - source-backed next-hop。
  - crash-site 排序且不丢结果。
  - 无 state 回退。
- `tests/test_static_aware_grep_read_glob.py`
  - GLOB ranked endpoint 前置。
  - GREP role/path_id/next READ。
  - READ Static context / record candidate 建议。
  - 无 raw dict/XML/新 observation heading。
  - 无 static state 时保持原行为。
- `tests/test_trace_fallback_extraction.py`
  - smoke evaluator 能识别 high-role GREP 与 static-aware READ。

### 指标预期

- `first_useful_read_step`：预计下降 1–2 个 action。
- `first_candidate_step`：对已有 ranked path 的任务预计下降 1–3 个 action。
- `candidate_set_miss`：预计比 v13 降低 15–25%，主要来自 wrapper/path-anchor next-hop。
- `CrashPathRecall@5`：与 Task 03 叠加后预计提升 3–7pp。
- 输出长度：每个 GLOB/GREP hit 最多一条渲染 hint；READ 最多 5 条，不新增 observation section。

### 剩余 smoke 验证

- `GT in static-aware GREP topK` / `GT in READ next-hop topK` 需要在 v14 smoke 结合离线 GT 统计。
- `record_sink_candidate after high-role READ rate` 已具备 trace 统计字段，但需要真实新 trace 才能形成 baseline。
- 当前排序只重排工具已经返回的候选集合；不会为了 annotation 扩大 GREP 搜索量。这保留了性能边界，但极低 `head_limit` 仍可能在 annotation 前截掉好候选。

## Definition of Done

- 经典工具输出仍短、可读、可回退。
- Top-K ranked path 文件在 GLOB/GREP 中明显前置。
- READ 能显示当前区域的 role/path/candidate 关系。
- 模型在 smoke trace 中更频繁使用 `READ(match_id=...)` 和 next-hop，而不是反复 broad GREP。
- observation 六段式不受影响。
