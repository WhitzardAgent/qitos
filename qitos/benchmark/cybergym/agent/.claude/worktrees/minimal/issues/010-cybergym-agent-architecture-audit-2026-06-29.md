# CyberGym Agent 全面架构审计报告

日期：2026-06-29  
审计对象：源码 commit `6a00a33` 及其依赖的 `../qitos` 运行框架  
重点：CyberGym Level 1 PoC 生成任务的策略架构、状态模型、反馈闭环、工具协议、上下文、测试与发布路径

> 面向 1507 个任务全集、以实际 crash PoC 成功率为中心的进一步分析与实施顺序，见
> [011-cybergym-task-solving-effectiveness-2026-06-29.md](011-cybergym-task-solving-effectiveness-2026-06-29.md)。

## 1. 结论先行

当前实现已经积累了不少正确方向：把 `submit_poc` 当作主要 oracle、保留原始证据、鼓励尽早构造候选、区分 entry-to-sink 路径和具体触发条件、提供专用搜索与二进制检查工具。这些都比通用 coding agent 更贴合 CyberGym。

但从架构质量看，系统已经进入了明显的“局部补丁叠加”阶段。最核心的问题不是某个 prompt 写得不够好，而是：

> **系统没有单一、可验证的认知状态和控制策略。工具结果、渲染逻辑、启发式分类、多个状态机和 prompt 指令同时在修改或解释策略，导致反馈可能错配、状态可能因渲染而变化、同一事实在不同模块拥有不同语义。**

这会带来三类直接风险：

1. **正确性风险**：并行工具结果可能被错误关联到其他 action；渲染 observation 本身会改变 gate 状态；模糊的 `no crash` 会被当成确定的 path failure 并自动反驳 gate。
2. **策略风险**：`PhaseEngine`、`control_mode`、`runtime_stage`、`candidate_required`、pending flags、schema filter 和 prompt reminder 同时控制行为，彼此存在可观测矛盾。
3. **演进风险**：103 个 state 字段、约 1.67 万行核心实现、21 个基础工具和 13 个测试构成了极不对称的复杂度；任何局部修复都容易在另一个控制面产生回归。

综合评级：**当前版本适合作为快速实验平台，不适合作为稳定、可解释、可持续优化的 benchmark agent 内核。** 建议先修复 P0 契约问题，再做“单一事件流 + 纯 reducer + 单一策略投影”的收敛式重构，不建议继续向现有状态字段和 prompt reminder 上追加补丁。

## 2. 审计范围与验证结果

本次检查覆盖：

- `agent.py` 的构造、prompt、prepare、reduce、候选和提交处理；
- `state.py` 的 103 个状态字段和兼容迁移；
- `agent_impl/phase.py`、`validation.py`、`prompts.py`、`observations.py` 的控制面；
- `submit_tool.py`、QitOS `ActionExecutor` 与 `ToolResult` 的端到端结果协议；
- `context.py` 的 snip、microcompact、span compact 和恢复逻辑；
- candidate family、tracking tools、repo index、adapter、CLI、环境和同步路径；
- 当前全部测试与 bundled runtime copy。

实际验证结果：

```text
PYTHONPATH=../qitos:.. python -m pytest tests -q
=> 1 failed, 12 passed

失败：test_vul_side_stop_criteria
原因：测试期望 vul-side crash 后停止，默认实现明确选择继续运行。

PYTHONPATH=../qitos:.. python -m py_compile agent.py state.py context.py submit_tool.py agent_impl/*.py
=> 通过

bash scripts/sync_to_qitos.sh
=> 失败：默认寻找 cybergym_agent/qitos，而本机 QitOS 实际位于 ../qitos
```

此外，截至最终复核，commit `6a00a33` 的开发源与 `../qitos/qitos/benchmark/cybergym/agent` 中的 bundled copy 在本轮涉及的 10 个核心文件上 checksum 全部不同。也就是说，“正在审查的当前源码”和“benchmark 实际 import 的代码”不是同一个构建物。

规模快照：

| 项目 | 当前值 |
|---|---:|
| 核心 Python 实现（含主要 mixin） | 约 16,675 行 |
| `CyberGymState` 字段 | 103 |
| 默认注册工具 | 21（delegate 模式还会增加） |
| 当前测试 | 13 |
| `context.py` | 2,259 行 |
| `agent.py` | 2,518 行 |
| `agent_impl/tools.py` | 2,314 行 |

## 3. 当前真实架构

当前系统并不是一个四阶段状态机，而是多个控制系统叠加：

```text
Task/Repo
  -> init_state：静态摘要、seed、格式推断、family bootstrap
  -> PhaseEngine：ingestion/investigation/formulation/verification
  -> control_mode：orienting/no_candidate/candidate_required/
                   candidate_ready/post_submit_miss/checkpoints/reflection
  -> runtime_stage：candidate family 调度阶段
  -> prompt projection：phase guidance + mode guidance + reminders
  -> tool schema projection：按状态隐藏/暴露工具
  -> tool-level validation：再次决定某工具是否可用
  -> QitOS ActionExecutor：串行或并行执行
  -> rendered string + side-channel structured payload
  -> reduce：解析结果、修改证据、gate、candidate、feedback、phase 和 mode
```

这些层并没有共享一个统一的 `PolicyDecision` 或严格不变量，因此“当前应该做什么”可能在 prompt、schema 和 validator 中得到三个不同答案。

## 4. P0：必须优先修复的架构问题

### P0-1 并行 action 的结构化结果关联协议是错误的

这是当前最危险的正确性问题。

自定义工具为了让模型看到更漂亮的文本，会返回 rendered string，同时把原始 dict 存在 agent 单槽缓存中：

- [`agent_impl/tools.py`](../agent_impl/tools.py#L74) 从 `runtime_context["action_id"]` 取关联键；取不到时写入 `_last_structured_output` 单槽。
- QitOS [`action_executor.py`](../../qitos/qitos/engine/action_executor.py#L480) 构造的 runtime context 根本不包含 `action_id`。
- QitOS [`_action_runtime.py`](../../qitos/qitos/engine/_action_runtime.py#L277) 又在 `ActionResult -> ToolResult` 时丢弃了 `action_id`，只保留 tool name、latency 和 attempts。
- reducer 在 [`agent.py`](../agent.py#L1500) 只能读取 `_last_structured_output`，第一个 result 会拿到最后一次工具调用的 payload，后续 result 甚至可能只剩字符串。
- `submit_poc` 更进一步使用模块级 `_last_submit_structured_output`（[`submit_tool.py`](../submit_tool.py#L18)），而该工具又明确标记 `concurrency_safe=True` 并鼓励一轮并行提交多个候选。

因此，当一轮并行执行多个 READ、搜索或 submit 时，模型看到的字符串可能正常，但 reducer 使用的结构化数据可能属于另一个 action。对于 submit，这会造成：

- PoC A 的 path/hash 与 PoC B 的 verifier 结果拼接；
- feedback history、best candidate、family score 和 duplicate fingerprint 被错误更新；
- 所有并行 submit 可能被当作最后一个完成的 submit；
- 后续所谓“根据 oracle 学习”建立在错误归因上。

**建议：**彻底删除 side-channel。工具应始终返回一个 canonical structured result；模型展示文本应由 QitOS 的 renderer/view 层从同一个 result 生成。若短期无法改框架，至少必须把 `action_id` 贯穿 runtime context、`ActionResult`、`ToolResult` 和 reducer，并用 per-action map 存储，禁止任何 module/global/last-value fallback。随后增加 action 顺序置换测试。

### P0-2 observation 渲染不是纯函数，会修改认知状态

[`agent_impl/observations.py`](../agent_impl/observations.py#L624) 在 `_constraint_board_lines()` 中：

- 修改 suggestion 的 `observation_count`；
- 删除过期 suggestion；
- suggestion 被“看到”三次后自动提升为 `ChainGate`；
- 直接改写 `state.call_chain_gates` 和 `state.suggested_constraints`。

而 [`agent.py`](../agent.py#L310) 每个普通 step 会先在 `_build_observation_packet()` 中调用一次 `_constraint_board_lines()`，随后为了 TUI 缓存又调用一次。因此一次模型 step 会把 `observation_count` 增加两次；一个 suggestion 通常只需 1.5 个 step 就会被自动提升。

更严重的是，“被渲染但模型没有反对”被解释为证据。模型没有专门的 reject action，甚至可能没注意到该段内容。重试 prompt、TUI fallback、调试预览或未来任何额外 render 都会改变任务状态。

这违反 agent 内核最重要的不变量：

> `render(state)` 必须幂等且无副作用；只有显式事件经过 reducer 才能改变 state。

**建议：**立刻删除渲染中的所有 mutation。suggestion 的接受、拒绝、过期只能由 reducer 根据 `EvidenceObserved`、`ClaimConfirmed`、`ClaimRejected` 等事件处理。增加测试：序列化 state，连续调用 `prepare()` 两次，序列化结果必须逐字节相同（允许单独的非语义 trace cache）。

### P0-3 oracle、成功条件和停止策略没有统一契约

当前同时存在三套成功语义：

1. `submit_tool.py` 的公开 `/submit-vul` 只返回 vuln-side 信息；有 API key 时又会调用私有 full verification。
2. [`state.py`](../state.py#L389) 的 `is_verified()` 要求 accepted 或 vul crash + fix clean。
3. [`stop_criteria.py`](../stop_criteria.py#L45) 默认 vul-only 模式下绝不因 vuln crash 停止；但 [`tests/test_agent.py`](../tests/test_agent.py#L98) 仍要求一个 `vul_exit=77, fix_exit=77` 的结果立即停止。

这不是普通测试过期，而是 agent 到底在优化什么没有定义清楚：

- 如果运行期只允许 vuln-side oracle，agent 不可能知道“precision 是否足够”。
- 当前 prompt 却不断告诉模型“固定版也可能 crash”“缩到 1-4 字节”，并把未知的 fix-side 结果包装成“partial success”。
- 对 UAF、NULL dereference、integer overflow、assertion 等任务，“1-4 字节最小溢出”并不是通用策略。
- 如果 benchmark 最终会对所有已提交 PoC 做差分验证，运行期合理目标应是维护一个有序 candidate portfolio，而不是假装自己知道某个 vul-only hit 离 strict acceptance 有多近。

**建议：**先写清唯一协议，再实现：

- `OracleObservation` 只包含运行期真正可见的字段；
- `RunSuccess` 与 `BenchmarkAcceptance` 分离；
- 明确 vul crash 后是立即停止、保留 best 后有限精炼，还是继续到预算耗尽；
- 所有策略只使用可见证据，禁止用未知 fix-side 事实构造确定性指导；
- 让 stop criteria、reducer、prompt、CLI 和测试引用同一个 `OraclePolicy`。

### P0-4 模糊反馈被伪装成确定证据，并会污染 gate

失败分类器把 `vul_exit_code in (None, 0)` 直接归类为 `path_not_reached`（[`agent_impl/feedback.py`](../agent_impl/feedback.py#L248)），随后 proximity 又把它解释为“parsed, path not reached”（同文件 L281-L308）。但 `exit=0` 最多只表示“没有观察到目标 crash”，不能证明：

- carrier 已成功解析；
- 进入过目标 parser；
- 只是某个 path gate 失败；
- harness 输入映射正确。

接着 `_refute_matching_gates()` 在没有执行覆盖证据时，会 fallback 到“反驳最早的 open gate”（[`agent_impl/feedback.py`](../agent_impl/feedback.py#L569)）。这会把一个低信息量的 miss 变成高置信度的错误知识。

ASAN 分类也存在同样问题：任意 ASAN memory corruption 在没有与 expected bug type、目标 location 或 patch 语义比较时，就可能被标为 `trigger_wrong_signature`；vul-only crash 则一律获得 proximity 4/5。这个 0-5 分数不是测量值，而是启发式标签，却被 observation 进一步渲染成 `IMPROVING/STALLED/DECLINING` 趋势并驱动 re-investigation。

**建议：**采用“观察—推断—决策”三层分离：

- 观察：exit code、signal、stderr frame、candidate hash；
- 推断：`carrier_maybe_invalid`、`target_reachability_unknown`，带 confidence 和 alternatives；
- 决策：下一步验证哪一个可区分假设。

任何单次模糊 miss 都不能自动 refute claim。只有直接 source evidence、覆盖/trace evidence，或能区分该 gate 的成对实验才能改变 claim 的支持/反驳状态。

## 5. P1：核心设计层问题

### P1-1 控制策略分散在至少七个平面

当前行为同时由以下对象决定：

- `current_phase`；
- `control_mode`；
- `runtime_stage`；
- `candidate_required`；
- `pending_reflection/pending_chain_checkpoint/pending_gates_checkpoint`；
- tool schema filter；
- 每个 tool 自己的 validator；
- one-shot reminder 和 prompt 文案。

已经出现具体矛盾：

- ingestion prompt 要求使用 `RepoMap` 和 `FindSymbols`（[`agent_prompts/phase/ingestion.md`](../agent_prompts/phase/ingestion.md#L3)），但 ingestion 的 layered schema 刻意不暴露这些 advanced tools；测试甚至明确断言 `RepoMap` 在 ingestion 不可见（[`tests/test_agent.py`](../tests/test_agent.py#L347)）。
- formulation prompt 同时说“无需确认所有约束，尽早提交”和“第一个 inferred gate 未确认时不要构造”（[`agent_impl/prompts.py`](../agent_impl/prompts.py#L189)）。
- verification phase 在 submit miss 后立即 transition 回 formulation（[`agent_impl/phase.py`](../agent_impl/phase.py#L124)）；`candidate_ready` 又优先覆盖 phase guidance。因此大量 verification-specific proximity 文案在最需要它的下一轮实际上不可达或被其他 mode 覆盖。
- prompt 中写“允许某个操作”并不代表 schema 有该工具；schema 有工具也不代表 tool validator 会放行。

**建议：**引入唯一的纯函数：

```text
derive_policy(run_state) -> PolicyDecision(
  intent,
  objective,
  allowed_capabilities,
  action_budget,
  blocking_reason,
  completion_condition
)
```

phase、prompt、schema 和 validator 都只能投影这个对象，不能各自再推导策略。validator 只负责安全和参数契约，不负责研究策略。

### P1-2 State 是 103 字段的“数据库 + 缓存 + UI + 兼容层”

`CyberGymState` 同时保存：

- 原始任务事实；
- 启发式派生字段；
- phase/mode/stage；
- 多套 candidate/attempt/feedback 历史；
- prompt cache 和 reminder；
- UI sidecar 数据；
- legacy compatibility；
- 同一事实的 typed field 与 `metadata` 镜像。

典型重复包括：

- `submitted_fingerprints` 与 `metadata["submitted_candidate_fingerprints"]`；
- `patch_diff/error_txt/harness_entry_confirmed/repo_archive_root` 与 metadata 镜像；
- `attempt_history`、`verification_history`、`failure_history`、`feedback_history`、`hot_feedback_window`、`attempt_history_compact`；
- `path_constraints` 与 `call_chain_gates`；
- `current_phase`、`control_mode`、`runtime_stage`、`candidate_required`。

结果是大量“迁移、fallback、同步”代码，但没有 centralized invariant checker。任何一个分支漏写一个镜像字段，下一轮就可能根据旧值决策。

**建议：**把 state 拆成五个明确对象，并尽量派生而非存储：

1. `TaskContext`：不可变；
2. `EvidenceLedger`：append-only、带 provenance；
3. `HypothesisGraph`：claims 与 evidence links；
4. `CandidatePortfolio`：candidate、parent、mutation、attempt、oracle observation；
5. `PolicyCursor`：唯一 intent 和预算。

hot window、best score、prompt sections、ready list 都应是 projection，不应作为独立真相长期存储。

### P1-3 Mixin God Object 隐藏依赖，模块边界只是文件边界

`CyberGymAgent` 继承 12 个 mixin。mixin 之间通过 `self._xxx()` 和 MRO 隐式调用，`ValidationMixin` 的注释甚至需要说明哪些方法在本 mixin、哪些依赖其他 mixin。代码虽然被拆到多个文件，但并没有形成清晰的组件契约。

这导致：

- 工具层直接调用策略层 validator；
- observation 层修改 state；
- feedback 层调用 crash parser、candidate family、artifact persistence；
- reducer 同时处理业务状态、调度、prompt reminder、trace 和 memory；
- 很难独立测试任一层。

**建议：**用显式组合替代横向 mixin：`OracleAdapter`、`EvidenceExtractor`、`CandidateStore`、`PolicyEngine`、`PromptProjector`、`ContextStore`。`CyberGymAgent` 只做 QitOS adapter，不承载领域实现。

### P1-4 “confirmed gate”并不代表经过证据确认

`record_gate` 允许模型直接传入 `status="confirmed"`，validator 不验证 status，不要求 evidence id，也不确认 `node_function` 真正存在。找不到 node 时会静默把 gate 绑定到 `node_order=0`（[`tracking_tools.py`](../tracking_tools.py#L510)）。`record_chain_node` 同样接受任意 status，并把 evidence 写成固定字符串 `record_chain_node by agent`。

因此当前 gate ontology 的核心承诺——“confirmed 来自源码证据”——在数据模型层并不成立。checkpoints 还会强迫模型花 step 写 bookkeeping；为了离开 checkpoint，模型有动机快速填一个看似合理的 gate。

**建议：**模型只能提交 `ClaimProposal`，必须引用 tool result/evidence span。confirmed 状态由 reducer 根据有效 evidence reference 产生；不存在的 node 应报错；chain order 必须来自显式 edge，而不是记录顺序。

### P1-5 启发式 belief 被拿来做硬门控

`InputFormatModel.magic_bytes` 可由描述、文件名或 corpus header 推断，但 `SubmitPoCTool.validate_input()` 会在 magic 不匹配时直接阻止提交（[`submit_tool.py`](../submit_tool.py#L136)）。这会误杀：

- 同一 harness 支持的其他格式；
- polyglot 或嵌套 container；
- parser 接受非标准 header 的漏洞路径；
- corpus 与当前 target 不匹配的任务。

类似问题也存在于 candidate-ready 时全面禁止读/改、checkpoint 时禁止构造、schema 分层隐藏工具。研究策略中的“建议”被实现成了基础设施层的“权限”。

**建议：**安全边界才使用 hard guard；研究 belief 只做 soft ranking。若必须 guard，需要 `confidence=verified`、直接 evidence 和显式 override 路径。

### P1-6 Prompt 不是稳定策略，而是重复、冲突的动态控制面

架构文档宣称“稳定 system prompt + 短 observation”，但 `build_system_prompt()` 每轮把动态 phase/mode guidance 注入 system prompt（[`agent.py`](../agent.py#L1432)），tool schema 也随状态变化。与此同时，同一规则在 base persona、phase markdown、tool usage、multi-action、observation allowed tools、reminder 和 validator error 中重复出现。

问题不是 token 数量本身，而是模型同时收到：

- 内部状态标签和自创 ontology；
- 多份略有差异的行动规则；
- 不一定真实可调用的工具说明；
- 强制 bookkeeping 与“快速 submit”相互竞争。

**建议：**system prompt 只保留稳定角色、oracle 协议、安全边界和少量行动原则。动态策略全部放进单一 `PolicyDecision` observation；只展示本轮真实可调用 capabilities；删除重复 workflow 教程。

### P1-7 Candidate family/multi-agent 层增加了大量复杂度，但主路径收益不清晰

默认 `helper_subagents_enabled=False`，但 state、reducer、family pool、candidate queue、feedback ranking 和 runtime stage 仍长期存在于 classic 主路径。与此同时，direct candidate 又被塞进 `direct-main` family。

这使单 agent 的简单循环也必须经过 family abstraction，但现有测试没有验证 family selection、branch、retire、queue drain 或与并行 submit 的组合行为。

**建议：**把 classic 模式降到最小 candidate portfolio。只有真正启用 delegate 的配置才加载 family scheduler；用 feature boundary 隔离，而不是让主 state 永久背负实验性字段。

### P1-8 Context 系统过度定制，且与 state memory 重复

`context.py` 用 2,259 行实现 raw externalize、snip、early-read snip、microcompact、segment summary、span replacement、post-compact restore、evidence index 和 circuit breaker。与此同时 state 又保存 durable facts、task memory、strategy ledger、feedback window、constraint board 和各种 compact history。

这形成多个独立的有损摘要层，无法回答“哪份是 canonical”。span summary 还可能直接在 history retrieval 内额外调用 LLM（[`context.py`](../context.py#L1861)），这类调用不经过主 agent 决策循环，成本、失败和可复现性难统一统计。

当前测试没有覆盖 context compaction。

**建议：**保留 append-only event/evidence store 和一个确定性 state snapshot。raw output 只保存一次；prompt 由 snapshot + 最近 N 个 events 生成。只有接近上下文上限时才做单一摘要，而且摘要也作为有 provenance 的 event，不再叠加四级自定义压缩。

### P1-9 工具面并不“窄”，且存在能力重叠

默认注册 21 个工具：搜索/阅读类 10 个、写入/编辑类 6 个、submit 1 个、tracking 4 个。对于“生成 raw input”任务，append/insert/replace-lines/str-replace 等通用源码编辑能力价值有限；`GREP/FindSymbols/CallsiteSearch/RepoMap` 也有重叠；tracking tools 把内部记忆管理暴露给模型。

工具越多，schema token、选择错误、别名兼容和状态门控组合越多。`build_tool_registry()` 遇到 `CodingToolSet` ImportError 还会静默 `pass`，可能让 agent 在没有核心工具的情况下继续启动。

**建议：**收敛到：`inspect_source`、`search_source`、`inspect_artifact`、`run_or_generate`、`write_candidate`、`submit_candidate`。内部记录由 reducer 自动完成。核心工具加载失败必须 fail fast。

### P1-10 静态 repo index 的能力边界没有被策略层正确对待

repo index 是 regex + brace-depth 实现，限制 500 个文件、5000 条 edge、backward BFS 深度 5，并且 indirect dispatch 实际返回空。它只适合提供候选位置，不足以证明真实 call path，尤其面对宏、函数指针、C++ overload、generated parser 和跨语言项目。

但初始化逻辑会据此自动选择最短 chain、赋予角色并创建 inferred format gate。这些推断随后参与 prompt completeness、checkpoint 和 gate repair，放大了静态索引误差。

**建议：**把 repo index 输出命名为 `SearchCandidate`，永远不直接形成 path truth；只有 source span 被读取并验证 edge 后才进入 evidence graph。

### P1-11 运行观测能力本身存在断点

构造函数先创建 exchange logger，随后在 `super().__init__` 后又把 `self._exchange_logger = None`（[`agent.py`](../agent.py#L151) 与 L225），因此 exchange logger 实际被禁用。多处 sidecar 写入又用宽泛 `except Exception: return` 静默失败。

已有 issues 也指出 `.agent/` 和 `.cybergym/` 不是 run-scoped。缺少可靠 run id、策略投影、state diff 和 candidate-result correlation 后，很多“agent 为什么失败”的判断只能靠人工读 prompt，而不能重放。

**建议：**每个 run 使用唯一目录；每步记录 `state_before + events + policy_decision + actions + results + state_after`；trace 写失败必须进入 telemetry；不要让调试代码修改业务 state。

### P1-12 开发源与运行副本是两个真相源

AGENTS 说明 benchmark import bundled copy，但默认同步脚本在当前仓库布局下无法找到 `../qitos`，且同步本身排除 tests。当前核心文件已经发生实质漂移。

这意味着：

- source 测试通过也不代表 benchmark 代码相同；
- benchmark trace 无法仅凭 source commit 复现；
- 手动同步是一个未校验的发布步骤；
- bundled copy 没有自己的 source hash/manifest。

**建议：**优先让 QitOS 以 package/editable dependency 引用唯一源码。若必须 vendor，则用确定性 build/sync 命令生成，写入 source commit/hash，并在 benchmark 启动时 fail-fast 校验。同步后的 copy 必须跑同一测试集，而不是排除 tests 后只做 `py_compile`。

### P1-13 测试结构不足以支撑当前复杂度

13 个测试主要检查 package surface、少量工具、ready queue 和单次 submit。缺失：

- 并行 action 结果关联；
- 多 submit batch；
- reducer 的 phase/mode 转移；
- prepare/render 幂等；
- state 序列化/恢复；
- gate lifecycle 和 provenance；
- context compaction；
- candidate family 调度；
- source/bundled parity；
- 完整的 task scenario replay。

现有唯一 stop semantics 测试还与默认实现相反。当前测试更像 smoke tests，不能作为架构契约。

**建议：**建立 model-free scenario tests：输入固定的 tool/oracle event 序列，断言 state、policy 和下一轮 capability。再增加 trace replay 与 property tests（幂等、顺序不变、候选关联唯一、derived fields 可重建）。

## 6. 本次静态审计发现的具体实现缺陷

这些不是报告的主轴，但它们能说明当前架构缺少集中不变量：

1. [`agent_impl/prompts.py`](../agent_impl/prompts.py#L237) 先追加“uncovered nodes”警告，随后 L245 无条件重写 `constraint_lines`，警告被丢弃。
2. [`agent.py`](../agent.py#L1756) 的 `path_not_reached` read budget reset 放在 `if gate` 的 `else` 分支内；该分支中 `gate` 必为空，所以 reset 永远不可达。
3. [`agent_impl/observations.py`](../agent_impl/observations.py#L336) 创建了 `trimmed = hot_feedback_window[-2:]`，但后续仍渲染完整 window；注释与行为不一致。
4. exchange logger 初始化后被覆盖为 `None`。
5. `record_gate` 声称 node 必须匹配，实际不匹配时静默绑定到 0。
6. `status` 参数在 tracking tools 中没有 enum validation，可写入任意状态，随后 query helper 可能永远看不到该记录。
7. 当前 stop test 失败，说明运行协议没有被测试固定。
8. 默认 sync command 在当前目录布局下失败。

这些缺陷应修，但更重要的是不要只修这些行；它们共同指向“逻辑分散、projection 有副作用、无 centralized contract”这一系统性原因。

## 7. 推荐目标架构

### 7.1 核心原则

1. **一个事件流**：所有状态变化来自 typed domain events。
2. **一个 reducer**：纯函数、确定性、可重放。
3. **一个策略投影**：prompt、schema 和 action validation 使用同一个结果。
4. **证据与推断分离**：不可把 heuristic 当事实。
5. **candidate-result 强关联**：以 immutable candidate id + content hash 关联 oracle。
6. **presentation 无副作用**：所有 render 都幂等。
7. **运行期只使用可见 oracle**：不模拟未知 fix-side 信息。

### 7.2 建议的数据模型

```text
RunState
├── task: TaskContext                         # immutable
├── evidence: EvidenceLedger                 # append-only
├── hypotheses: HypothesisGraph
│   ├── Claim(id, statement, confidence)
│   └── EvidenceLink(claim_id, evidence_id, supports/refutes)
├── candidates: CandidatePortfolio
│   ├── Candidate(id, hash, path, parent_id, mutation)
│   └── Attempt(candidate_id, oracle_observation_id)
└── cursor: PolicyCursor(intent, budget, last_progress_event)
```

`confirmed/open/refuted` 不再是模型随意填写的字符串，而是根据 evidence links 派生。`best candidate`、`hot feedback`、`ready list` 和 `proximity` 都是 projection。

### 7.3 建议的事件流

```text
QitOS ToolResult (structured, action_id preserved)
  -> normalize_event()
       SourceRangeObserved
       CandidateCreated
       CandidateSubmitted
       OracleObserved
       ClaimProposed
       ClaimEvidenceAdded
  -> reduce(state, event) -> new_state
  -> derive_policy(new_state) -> PolicyDecision
  -> render_prompt(policy, state_snapshot)
  -> expose_tools(policy.allowed_capabilities)
```

这里没有工具结果 side-channel，没有 renderer mutation，也不需要多个 mode 各自推导规则。

### 7.4 更适合 CyberGym 的策略循环

建议把四阶段改成一个 progress-driven loop，而不是时间驱动 phase：

```text
ORIENT
  得到 harness/input mapping 的最小证据
TRACE
  找到一个可检验的 entry→sink 假设和至少一个 trigger claim
CONSTRUCT
  基于当前最优 hypothesis 生成一个有明确差异的 candidate
SUBMIT
  记录 immutable candidate-result pair
DIAGNOSE
  选择能最大区分剩余假设的下一动作
  -> TRACE 或 CONSTRUCT
```

转移条件应是 progress evidence，不是“读了 8 次”“过了 10 step”或“某段 observation 被看到三次”。时间/step 预算只用于选择更激进或更保守的动作，不用于伪造知识。

## 8. 推荐迁移路线

不建议 big-bang rewrite。可以用 strangler 方式逐步替换。

### 阶段 A：P0 契约止血（1-3 天）

1. 工具统一返回 structured result；删除 `_last_structured_output` 和 module-global submit buffer。
2. 把 action id 贯穿 QitOS execution/result contract；增加并行顺序测试。
3. 让所有 observation/prompt renderer 纯化；删除 auto-promote mutation。
4. 定义唯一 oracle/stop policy，修正测试。
5. 暂停自动 gate refutation 和 proximity trend；只展示原始观察与带 confidence 的推断。
6. 把 heuristic magic mismatch 从 hard fail 改为 warning。
7. 修复本报告第 6 节的确定性缺陷。

### 阶段 B：收敛控制面（约 1 周）

1. 新增 `DomainEvent`、纯 reducer 和 `derive_policy()`。
2. 让 prompt/schema/validator 全部消费 `PolicyDecision`。
3. 移除 `control_mode/runtime_stage/candidate_required` 的重复真相，只保留兼容 projection。
4. 为 candidate、attempt、oracle 建立 immutable id/hash 关联。
5. 加入 model-free scenario replay tests。

### 阶段 C：收缩状态和工具（1-2 周）

1. 引入五对象 state，逐步废弃 103 字段大表。
2. tracking tools 改为 reducer 自动记录；模型只提 claim，不直接写 confirmed truth。
3. 合并重叠搜索/二进制检查工具；去掉无关编辑工具。
4. classic 与 multi-agent family scheduler 做真正的 feature isolation。
5. context 收敛到 event log + evidence snapshot + 最近窗口。

### 阶段 D：发布与基准闭环

1. 消除 source/bundled 双真相，或加入强制 hash 校验。
2. 每次改动跑固定 task cohort 和 trace replay。
3. 不只看 pass rate，还看：
   - first candidate step；
   - first vuln crash step；
   - 每次 submit 带来的 hypothesis entropy reduction；
   - duplicate/invalid candidate 比例；
   - candidate-result correlation 错误数（必须为 0）；
   - prompt token 与额外 compaction LLM 调用成本；
   - strict pass@1 与 project/bug-class 分层结果。

## 9. 必须建立的验收不变量

重构是否成功，不应只靠“prompt 看起来更合理”。至少要自动验证：

1. 连续两次 `prepare(state)` 不改变业务 state。
2. 对独立并行 actions，交换完成顺序不会改变最终 state。
3. 每条 oracle observation 恰好关联一个 candidate id、path 和 content hash。
4. 任意 confirmed claim 都能追溯到一个存在的 evidence id/source span。
5. 模糊的 no-crash observation 不会自动 refute claim。
6. prompt 中展示的 allowed tools 与实际 schema、validator 完全一致。
7. state 可序列化、恢复并重放到相同 policy decision。
8. runtime bundled hash 与 source build hash 一致。
9. 所有核心路径测试通过，且不存在默认环境下失败的契约测试。
10. trace 写入失败可观测，不会静默吞掉。

## 10. 最终判断

当前 agent 的主要瓶颈已经不是“缺少更多安全知识提示”或“再调一个 read/submission 阈值”。继续在现有架构上增加 gate 类型、proximity 分数、reminder 和 state 字段，会进一步放大控制冲突和不可验证性。

最值得保留的是：

- submit-feedback 迭代方向；
- 原始证据外置；
- source/search/binary 专用能力；
- candidate hash、attempt history 和 entry-to-sink 假设这些领域概念。

最需要替换的是：

- rendered string + side-channel dict 的工具协议；
- observation 驱动的状态 mutation；
- 多重 phase/mode/stage 控制；
- 把启发式分类当确定事实的 gate/proximity 系统；
- 103 字段 state 和 mixin god object；
- 手工同步的双源码发布方式。

一句话的重构目标是：

> **把当前“由 prompt、启发式和多个状态标签共同暗示下一步”的 agent，改造成“由可重放证据事件驱动、用单一策略函数明确决定下一步”的 agent。**

这会比继续微调 prompt 更直接地提升正确性、可解释性和 benchmark 优化效率。
