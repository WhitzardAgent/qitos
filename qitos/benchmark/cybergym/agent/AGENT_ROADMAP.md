# CyberGym Agent vNext Roadmap

## 1. 目标

vNext 要解决的不是“缺少静态分析框架”，而是现有能力没有形成一条完整、可验证的专家工作流。唯一 north-star 是提高 PoC 使目标程序发生预期 crash 的比例；最关键的中间目标是提高真实 crash site/causal site 在 sink candidates 中的 Top-K coverage：

```text
任务描述
  -> LLM 结构化描述证据
  -> 仓库内引用验证
  -> harness 输入消费模型
  -> entry 到危险操作的候选路径
  -> 基于描述、可达性和风险语义的路径排序
  -> sink 关键参数回溯到输入字节
  -> 更早生成候选 PoC，并用 submit_poc 反馈迭代
```

基准统计表明，很多任务不会直接给出函数、文件或触发条件。因此系统必须把自然语言线索当作“待验证的导航先验”，不能把它们直接当成源代码事实，也不能在没有代码证据时自动确认 sink。

## 2. 对当前实现的校准

原路线图将若干能力列为“待新建”，但当前代码已经提供了它们的主体。vNext 必须扩展现有主链，避免并行实现第二套图、第二套 sink registry 或第二套分析状态。

| 能力 | 当前实现 | vNext 判断 |
|---|---|---|
| 描述解析 | `task_spec.py`、`agent_impl/task_analysis.py` 使用正则/关键词；`set_crash_type` 只记录 crash type | 缺少 LLM 结构化描述和逐条代码验证 |
| 仓库结构索引 | `analysis/indexer.py` + `AnalysisService.index_repository()` 已用 tree-sitter 建索引 | 复用，不另建索引 |
| 调用图与路径 | `analysis/service.py` 已维护 `edges`、`entry_paths`，并提供 `find_paths_to_target()` | 复用；新增“从 entry 自动枚举到风险端点的路径” |
| sink 导航排序 | `_navigation_rows()`、`discover_sink_navigation_leads()`、`reachable_functions_from_entry()` 已综合可达性、input control、risk signal、crash type | 合并重复排序逻辑，并改用结构化描述证据 |
| harness 识别 | `agent_impl/repo_index.py` 发现 entry/direct calls；`agent_impl/harness.py` 解析 target 与 input format | 缺少函数体级 consumption pattern 与字段证据 |
| sink 分析 | `analyze_sink_candidate()` 已求 entry-to-target path、约束、sink risk 和参数 provenance | 缺少按 crash type 选择关键参数和稳定的输入字节映射 |
| 约束模型 | `constraint_*`、`ChainGate`、`ConstraintIR.input_mapping` 已存在 | 扩展现有 IR，不再引入平行 Gate 类型 |
| 反馈闭环 | `submit_poc` 是 oracle，失败会触发 gate/候选调整 | 保持；新分析只服务于更早、更准的候选生成 |

### 明确废弃的旧方案

以下内容不再作为 vNext 设计：

- 不新建独立的 `agent_impl/sparse_graph.py`。`AnalysisService` 的 `symbols/summaries/edges/entry_paths` 是唯一程序图来源。
- 不复制一套外部 TSA 的 `xref.py`、`semantic_search.py`、`class_hierarchy.py` 后再接线。先用当前索引的 unresolved-call 指标定位缺口，再增量增强 `analysis/callee_resolution.py`、`analysis/call_graph.py` 和 `analysis/indexer.py`。
- 不新增与 `analysis/vuln_patterns.py`、`default_api_models()` 重叠的全局 `sensitive_ops.py`。危险操作语义应收敛到一个 registry，由导航、sink 分析和参数选择共同读取。
- 不把描述中提到的函数自动升级为 confirmed sink。描述只能产生 prior；代码可达性、风险信号或模型 READ 后的 `record_sink_candidate` 才能升级状态。
- 不做完整 taint、SMT、statement-level slicing 或模板化自动 PoC 生成。

## 3. vNext 子任务

实施规划拆成 5 个任务，详细说明见 [`vnext/README.md`](vnext/README.md)。

所有任务还必须遵守 [`vnext/CONTEXT_PROMPT_DESIGN.md`](vnext/CONTEXT_PROMPT_DESIGN.md)：分析能力只有进入固定六段式 observation、phase prompt、delta/compaction 恢复和 end-to-end context 测试后才算完成。

Sink 定义、全量数据结论、专业漏洞规则、TSA 选择性移植和收益预测见 [`vnext/SINK_DISCOVERY_AND_METRICS.md`](vnext/SINK_DISCOVERY_AND_METRICS.md)。

| 顺序 | 子任务 | 交付结果 | 依赖 |
|---|---|---|---|
| 1 | [结构化描述与代码引用验证](vnext/01-description-analysis-and-ref-verification.md) | LLM 线索成为有 provenance 的 typed state；进入 Current Assessment 并驱动 ingestion prompt | 无 |
| 2 | [Harness 输入消费模型](vnext/02-harness-consumption-model.md) | AST 提取输入模式；紧凑进入 Mission/Current Assessment | 无；可与 1 并行 |
| 3 | [候选漏洞路径生成与排序](vnext/03-ranked-vulnerability-paths.md) | 多通道召回真实 crash/causal sites，目标 ExactSinkRecall@5 68–76%；成为 Vulnerability Path 唯一来源 | 1、2 |
| 4 | [Sink 关键参数到输入字节映射](vnext/04-sink-argument-input-mapping.md) | 输出 source-backed byte mapping；进入 Required Conditions 和 formulation prompt | 2、3 |
| 5 | [旧启发式收敛、Context 审计、评测与发布](vnext/05-cleanup-evaluation-rollout.md) | 清理重复逻辑，修复六段式偏差，建立 context/能力指标与灰度开关 | 1–4 |

执行时先交付 Task 05 的一个只读 evaluator 切片（冻结 manifest、project-held-out split 和 baseline），再开始 01/02。完整清理与发布仍在最后执行。这样每项实现从第一天起都能用 sink recall 和 crash-path coverage 验证，而不是全部做完后才发现方向错误。

## 4. 统一数据流与所有权

### State 层

`state.py` 是模型可见、可序列化结果的唯一所有者。新增数据必须用 dataclass 表达，并在 `CyberGymState.__post_init__()` 中支持 dict 反序列化：

- `DescriptionAnalysis`：LLM 对描述的解释；状态为 `pending | recorded | verified | partial`。
- `VerifiedCodeRef`：一个搜索 hint 对应的代码命中及置信度；明确区分 exact、case-folded 和 symbol-index 命中。
- `HarnessConsumptionModel`：选中 harness 的输入消费模式及 source spans。
- `RankedVulnerabilityPath`：候选路径的稳定 ID、节点、端点和 score breakdown。
- `InputByteMapping`：sink 参数/约束与输入 offset、width、endianness 的映射；未知字段必须保持 `unknown`，不能猜值。

体积较大的完整分析结果继续存放在 `ArtifactStore`；state 只保留 Top-K 摘要、ID 和用于 prompt 的紧凑证据。

### Analysis 层

`analysis/service.py` 继续作为自动静态分析的门面：

- 索引、符号解析、call edges、entry paths 不迁移。
- description/harness 作为 query prior 传入，不能改变 source-backed facts。
- 所有 score 必须返回 breakdown，避免单一 opaque confidence。
- 部分索引和 unresolved call 必须显式传播为 `partial`/`gaps`，不能伪装成“不可达”。

### Agent 编排层

- `init_state()` 只做确定性的 bootstrap，不调用 LLM。
- LLM 在 ingestion 调用 `analyze_description(...)` 写入结构化结果。
- `reduce()` 看到 description dirty flag 后，调用静态服务验证 refs、重算导航；与 `_pending_sink_analysis` 的模式一致。
- observation 只展示 verified refs、Top-K paths 和 source-backed mappings；原始大结果不塞入 prompt。

### Context 与 Prompt 层

遵循 `context_design.md`，模型面对的不是 controller state dump，而是固定六段式 Investigation Brief：

- `Mission`：任务、crash type、紧凑输入消费摘要。
- `Current Assessment`：verified description refs、harness/sink 的 confirmed/likely/unknown/rejected 状态。
- `Vulnerability Path`：active path、Top alternatives、逐节点 gate 状态。
- `Required Conditions`：关键参数来源、input byte mapping、open/refuted conditions。
- `Experiments`：submit oracle 结果及其对 path/gate/mapping 的修正。
- `Next Action`：当前唯一 blocker、具体目标和 stop condition。

不得追加独立的 Description/Harness/Static Analysis/Byte Layout section。phase prompt 只告诉模型如何消费上述 section，不重复动态事实。

当前 `_render_observation()` 仍产生 `Foundation`、`Allowed Tools`，`_render_required_conditions()` 还可能产生嵌套的 `## Constraint Analysis Diagnostics`。这些属于已确认的 V13 技术债，Task 05 必须收敛到六段式。

新分析事实必须存 typed state/ArtifactStore；history 中的 READ/GREP/trace 原始输出即使被 snip/microcompact，也能在下一次 observation 或 compaction 恢复后从 state 重建。description/harness/active path/first confirmed mapping/feedback revision 是强制 full refresh 的 semantic events。

## 5. 里程碑与完成定义

### M0：指标与离线 oracle 固定

- `error.txt` 只作为离线 label，绝不进入 Level-1 runtime context。
- 固定 project-level held-out split，并报告 ExactSinkRecall@1/3/5、CrashPathRecall@5、CausalCoverage、GraphDistanceToGT。
- 同时报告 `crash/completed` 与 `crash/all_started`，避免 running/timeout 扭曲结果。
- 能把失败归因到 candidate-set miss、ranking/prompt selection、gate/mapping 或 submit conversion。

### M1：描述和 harness 证据可用

- ingestion prompt 明确要求一次 `analyze_description`。
- 描述线索分为 verified/unresolved，而不是直接生成高置信 sink。
- 选中的 harness 显示 consumption pattern、first hops 和关键 source span。
- 序列化旧 state 不报错。

### M2：Top-K 候选路径替代“函数菜单”

- 内部先保留 30–50 个轻量 endpoints，再多样化为模型可见 Top-5；不能在图搜索早期只截断 5 个。
- bootstrap 先跑 depth≤8 fast tier；候选不足/只有 wrapper/存在关键 unresolved 时自适应扩到 depth≤24。
- 每条路径包含 reachability、risk semantics、description match、resolution quality 的分项分数。
- 模型能从 observation 直接选择路径并 READ 对应 endpoint/callsite。
- partial graph 不会把未知路径误标为 eliminated。

### M3：关键参数形成 PoC 字节约束

- `record_sink_candidate` 后的自动分析只优先回溯 crash-type 对应参数，而不是无差别展示所有参数。
- 可证明时输出 offset/width/endianness；不可证明时输出 unresolved reason 和下一查询。
- 映射进入 `requirements`/`ChainGate`，并能在 submit miss 后被 refute/revise。

### M4：清理与发布

- `_symbol_mentions()`、`_extract_search_anchors()`、`_classify_bug_type()`、`_extract_affected_component()` 只作为未完成 description analysis 时的 fallback。
- `_generate_sink_candidates()` 不再从未验证的描述 token 自动制造可晋级候选。
- `discover_sink_navigation_leads()` 与 `reachable_functions_from_entry()` 使用同一 scorer/registry。
- 现有测试、新增单元测试和评测脚本通过，且同步后的 QitOS bundled copy 可导入。
- held-out ExactSinkRecall@5 至少达到 60% 才允许默认启用；目标区间为 68–76%。
- completed crash rate 保守目标 78–81%，合理目标 81–84%；若 sink recall 上升但 crash rate 不升，必须定位 context selection 或 mapping conversion 瓶颈。

## 6. 跨任务验收原则

每个任务都必须满足：

1. **证据可追溯**：任何事实包含文件、行号或明确 provenance；description prior 必须标注为 prior。
2. **失败可降级**：parser/index 超时返回 partial 和 next action，不阻塞 candidate generation。
3. **状态可恢复**：新增 dataclass 可从历史 dict state 恢复；stable ID 在同一 graph fingerprint 下不漂移。
4. **prompt 有预算**：只展示 Top-K 摘要，完整结果留在 artifact store。
5. **候选优先**：新分析不能把 `candidate_required` 变成死锁；没有完整 mapping 时仍允许生成并提交 PoC。
6. **oracle 优先**：`submit_poc` 的真实反馈覆盖 description prior 和静态分析推断。
7. **六段式落地**：新信息必须进入既有 section，不得新增 orphan `##`；prompt 只引用真实 section 名。
8. **压缩后保真**：active path、第一 blocker 和 concrete byte mappings 在 snip/span compaction 后仍可由 state 重建。

## 7. 统一验证命令

源代码任务完成后运行：

```bash
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym \
  python3 -m pytest tests -q

bash scripts/sync_to_qitos.sh

cd /data/pxd-team/workspace-149/zwq/qitos-cybergym
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym \
  python3 -m py_compile qitos/benchmark/cybergym/agent/agent.py
```

任务 1–4 还必须运行各自文档列出的定向测试；任务 5 负责最终全量回归和评测对比。
