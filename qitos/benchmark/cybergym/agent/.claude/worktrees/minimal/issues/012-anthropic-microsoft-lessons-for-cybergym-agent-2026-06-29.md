# Anthropic 与 Microsoft 公开实践对 CyberGym Agent 的设计启示

日期：2026-06-29  
实现基线：源码 commit `6a00a33`，并纳入当前工作树中的 `chain_construction / poc_iteration` 两模式改动  
任务范围：CyberGym 全部 1507 个任务；目标是从漏洞描述出发，探索代码库并提交可触发 crash 的 raw-input PoC  
关联报告：[010 架构审计](010-cybergym-agent-architecture-audit-2026-06-29.md)、[011 解题有效性专项分析](011-cybergym-task-solving-effectiveness-2026-06-29.md)

外部参照：

- [Anthropic defending-code-reference-harness](https://github.com/anthropics/defending-code-reference-harness)
- [Anthropic pipeline 说明](https://github.com/anthropics/defending-code-reference-harness/blob/main/docs/pipeline.md)
- [Anthropic《Using LLMs to secure source code》](https://github.com/anthropics/defending-code-reference-harness/blob/main/docs/blog-post.md)
- [Microsoft《Beyond the benchmark: Advancing security at AI speed》](https://www.microsoft.com/en-us/security/blog/2026/06/17/beyond-the-benchmark-advancing-security-at-ai-speed/)

说明：本文把两套公开材料视为外部设计证据，而不是可直接复制的答案。Anthropic 的目标主要是未知漏洞发现、验证、去重和修复；Microsoft 同时覆盖生产漏洞工作流和 CyberGym。当前仓库的目标更窄：在有限 step/budget 下解决一个已给漏洞描述的 PoC 构造任务。因此需要迁移机制，而不是照搬组织形态。

## 1. 结论先行

两套实践给出的最重要共同结论是：

> **模型只是推理组件；决定 PoC 成功率的是模型周围能否持续产出可信的 scope、entry-to-sink 路径、执行契约、可归因候选和独立验证结果。**

这进一步验证了 010/011 的主判断，同时改变了若干改进项的优先级：

1. **Prepare 不是“初始化字段”，而是一个必须输出可验证产物的阶段。** Microsoft 报告其主要增益集中在 prepare 和 scan；当前 agent 的初始化却会从短描述制造伪 symbol、伪 format、伪 strategy。
2. **PoC prove 是当前主战场。** Microsoft 将剩余 52 个失败中的 34 个（65.4%）归因于 prove 阶段；复杂结构输入、错误 fuzz 策略、环境不一致和构建超时是主要原因。这与全量任务分析完全吻合。
3. **静态“理解了”不能替代可执行路径。** 当前 `chain_completeness_score` 可由模型自报 node/gate 填高，没有证据强制，也没有验证 harness 真的消费了对应字节；它不是 Microsoft 所说的 concrete execution path。
4. **候选生成者不应同时充当事实裁判。** Anthropic 用同一镜像的新容器做独立验证，只允许 PoC 字节跨边界。当前 agent 让同一模型生成、解释、更新 gate，并从模糊 submit 结果推断失败位置，确认偏差很强。
5. **动态反馈不应只有“crash/no crash”。** Microsoft 展示的 instrumentation + hill climbing 把“理解完整 codec”改写为“搜索某个漏洞相关值越过阈值”。当前 no-crash 经常被伪造为 gate proximity；正确做法是主动构造可测量 probe。
6. **并行不是第一优先级。** Anthropic 的并行建立在明确攻击面分区、隔离容器、候选去重、串行 judge 和可靠 artifact contract 上。当前 action/result attribution 尚不可靠，直接扩大 parallel submit 会放大错误。
7. **不能用 96.5% 标题数字掩盖口径。** Microsoft 明确将其定义为 `any crash`，包含 target 和 non-target vulnerability。我们的评估必须分别记录 any-crash、target-crash 和严格验收，不能把三者混为一谈。

对当前实现最有价值的新设计，不是增加一个更大的“安全 agent”，而是增加四个窄而硬的系统构件：

```text
ScopeMap
  + HarnessContract
  + ProveStrategyRouter / ProbePlan
  + IsolatedCandidateVerifier
```

它们必须建立在 P0 的 action/result/oracle 契约修复之上。

## 2. 两个来源分别证明了什么

### 2.1 Anthropic：把 discovery 与 verification 做成 artifact pipeline

Anthropic 参考实现的自主流程是（见其 [pipeline 说明](https://github.com/anthropics/defending-code-reference-harness/blob/main/docs/pipeline.md)）：

```text
recon -> find -> grade/verify -> dedupe/judge -> report -> patch -> re-attack
```

与当前 CyberGym 直接相关的机制有：

- recon 先把不可信输入攻击面划分成相互独立的子系统，再把 focus area 交给 finder；
- finder 读取源码、构造输入并在 sanitizer 环境中运行，只有稳定复现的 PoC 才形成 crash artifact；
- verifier 使用**同一个已构建镜像**启动**全新容器**；finder 修改过的文件系统和对话历史不进入 verifier；
- finder/verifier 之间只传 PoC bytes 和最小复现契约，而不是 finder 的整段结论；
- finder 被要求本地复现 3/3，并排除 OOM、timeout 和启动失败等伪 crash；
- 多个 finder 共享已发现 crash 的签名，提交前去重；judge 串行处理，以避免并发去重竞态；
- 配置明确描述 target 如何构建、如何运行、什么信号算发现、PoC 长什么样；
- 动态执行处于隔离环境中，并限制网络出口。

它展示的不是“多 agent 天然更强”，而是一种清楚的信任边界：

```text
finder 的推理：不可信建议
PoC bytes：待验证 artifact
fresh-container execution：事实来源
grader verdict：受 schema 和复现规则约束的派生结论
```

这正是当前 agent 缺失的层次。

### 2.2 Microsoft：CyberGym 剩余失败主要发生在 PoC prove

Microsoft 公开了比 headline 更有用的失败归因。其系统在 1507 个 CyberGym 任务上报告 96.5% `any crash`，但剩余 52 个失败按阶段分布为（见 [Microsoft 官方博客](https://www.microsoft.com/en-us/security/blog/2026/06/17/beyond-the-benchmark-advancing-security-at-ai-speed/)）：

| 阶段 | 失败数 | 占剩余失败 |
|---|---:|---:|
| Scan | 8 | 15.4% |
| Validate | 10 | 19.2% |
| Prove | 34 | 65.4% |

其 prepare/scan 增益来自四类工程改进：

1. 按角色区分待审计代码、上下文代码和依赖，而不是按目录来源粗分；
2. 更完整地识别不可信输入入口，包括主源码树外的 maintainer fuzz harness；
3. 提升 call graph 的正确性和鲁棒性；
4. 根据任务把工作路由到相关 specialized agent，并过滤明显不相关路线。

剩余 prove 失败集中在：

- IVF/AV1、WPG、字体、PDF 等强结构输入；
- fuzzing 直到 timeout，却不能产生被目标接受的输入；
- 本地 crash 无法迁移到评测 harness；
- 选择了错误 target/build/runtime path；
- 构建复杂或超过时间预算。

Microsoft 对 52 个失败任务的后续实验还观察到三种 prove 策略：

```text
code-based path reasoning
fuzzing-based search
custom instrumentation-based search
```

不同模型都会使用三种策略，差异主要在于**对哪个 target 选择哪一种策略**。在 `arvo:61902` 中，成功路线不是完整理解 codec 后手工构造，而是通过 instrumentation 暴露漏洞相关变量，把问题转为搜索该值直到越过阈值的 hill-climbing。这一案例和三类策略均来自同一篇 [Microsoft 实验说明](https://www.microsoft.com/en-us/security/blog/2026/06/17/beyond-the-benchmark-advancing-security-at-ai-speed/)。

这对当前 agent 的启发非常直接：`poc_strategy = text/binary_python/corpus_mutate/hex` 只描述“怎么写文件”，没有描述“怎么求解触发条件”。它不是一个真正的 proof strategy router。

## 3. 两套实践与当前问题的交叉映射

| 外部原则 | 当前实现状态 | 直接风险 | 应迁移的设计 |
|---|---|---|---|
| Prepare 产生精确 scope | description heuristic 自动填充 symbol/format/strategy | 从第一步开始查错模块、错输入族 | `ScopeMap`，保留来源、角色、置信度和 unknown |
| 识别真实 untrusted entry | harness mapping 不是强制 artifact | 找到 sink 也无法让提交字节到达 | `HarnessContract` |
| 鲁棒 call graph | regex/index + 模型自报 chain/gate | speculative chain 被当成 confirmed | evidence-backed `RouteGraph` |
| discovery/verification 隔离 | 同一 agent 生成并解释结果 | 自证、错误 gate 更新、确认偏差 | deterministic/isolated candidate verifier |
| same-image fresh verifier | 本地路径与官方 submit 环境可能不同 | local crash 不可迁移 | environment fingerprint + parity check |
| artifact-first | 大量 ledger、reflection、phase label | 叙事状态增长，但 PoC 证据不增长 | candidate experiment record |
| 稳定复现 | 成功/失败主要看一次 submit | flaky crash、启动错误、超时混入 | 本地 repro policy + typed result |
| 分区后并行 | candidate family 与 parallel submit 可先于可靠归属 | 重复搜索和结果串线 | 先 repair contract，再做 partitioned exploration |
| prove strategy routing | 路由按文本/二进制/hex | 无法解决结构化约束和状态路径 | code/fuzz/instrument/state-sequence router |
| instrumented feedback | no-crash 被估计成 proximity/gate miss | 从低信息结果制造假知识 | explicit `ProbePlan` 与测量值 |
| 去重与串行 judge | path/logical fingerprint 混合，submit 有全局槽 | 重复候选、错误 candidate-result pair | byte hash + parent/delta + crash fingerprint |
| benchmark 公平边界 | corpus-first 与项目记忆边界不清 | 已知 PoC 泄漏或评估不可比 | seed provenance policy |

## 4. 对当前两模式架构的具体判断

当前工作树把四 phase 收敛成：

```text
chain_construction -> poc_iteration
```

方向上比“ingestion/investigation/formulation/verification + 多个 control_mode”更容易理解，但它还没有实现外部实践强调的证据边界。

### 4.1 `chain_completeness_score` 衡量的是填表完整度，不是路径真实性

当前分数由以下因素组成：

- 是否存在 `entry` node；
- 是否存在 `sink` node；
- node 是否标记 `confirmed`；
- node 是否有 `confirmed` gate；
- 是否存在 `confirmed format_gate`。

问题是这些状态主要由模型通过 tracking tool 写入，并不强制包含：

- 精确文件、行号和 source span hash；
- callsite/registration edge 证据；
- harness entry 对应的真实调用方式；
- 输入 slice 如何传播到目标参数；
- 可执行 probe 是否观察到该路径；
- verifier 是否独立复核。

因此模型可以通过“记录 entry、sink、若干 confirmed gate”把分数推过 0.4，而没有证明提交字节能到达 sink。这与 Microsoft 所说的 concrete execution path 恰好相反。

建议删除“状态标签直接贡献 completeness”的做法，改成 artifact invariant：

```text
RouteEdge.confirmed =
  source_span exists
  AND caller/callee or registration relation is parseable
  AND input mapping is explicit
  AND provenance is tool_result_id
```

`completeness` 只能从这些不可伪造的 typed records 派生；模型不能直接设置 `confirmed`。

### 4.2 强制 step 12 转入 PoC 仍然不是合理的 stage boundary

外部材料都支持“尽快运行 PoC”，但不支持“到固定 step 就假装准备完成”。固定 deadline 在简单任务上可能有益，在描述模糊、构建复杂或结构输入任务上会强制产生无信息候选。

更好的转移条件是：

```text
进入 prove，当且仅当：
  已有 HarnessContract
  AND 至少一个可执行 CandidateClaim
  AND 能说明 candidate 改变哪个输入控制量

若预算耗尽但上述条件不成立：
  生成最小 diagnostic probe，而不是随机 PoC
```

早候选仍然重要，但候选必须是实验，不是 deadline 的副产品。

### 4.3 `proximity_score` 不能决定退回 chain construction

当前退回逻辑包含“连续若干 submit 的 proximity 为 0/1”。但当前 oracle 通常只知道 crash/no-crash、timeout、启动错误和有限运行输出；它不能可靠判断离 sink 有多远。用这个分数驱动 phase 是把推测变成控制信号。

应当只有以下 typed event 能触发路线重构：

- `HARNESS_REJECTED_INPUT`：carrier/format contract 错；
- `ENTRY_NOT_OBSERVED`：harness/entry mapping 错；
- `TARGET_BRANCH_NOT_OBSERVED`：path predicate 未满足；
- `BAD_STATE_NOT_OBSERVED`：触发值未满足；
- `LOCAL_CRASH_REMOTE_MISS`：environment/harness parity 错；
- `BUILD_OR_LAUNCH_FAILURE`：基础设施问题，不应改写漏洞假设；
- `UNINFORMATIVE_NO_CRASH`：不更新任何 gate，只要求设计更有区分力的 probe。

这些 event 必须来自实际 instrumentation、coverage、sanitizer 或明确进程结果；不能由模型从空白输出补全。

### 4.4 两模式仍与多个旧控制面并存

增加 `agent_mode` 后，系统仍保留 `current_phase`、`control_mode`、`runtime_stage`、`candidate_required`、pending checkpoints 和 validator gating。`sync_phase_from_mode()` 只是把一个状态翻译成另一个状态，没有消除第二真相源。

外部实践的启示不是再增加一个 mode，而是让 pipeline stage 成为 artifact lifecycle：

```text
PreparedTask -> LocalizedFinding -> CandidateExperiment -> VerificationResult
```

每个 artifact 有 schema、producer、provenance 和 validator；UI/prompt mode 从 artifact 缺口派生，不再持久化多份控制状态。

## 5. 推荐目标架构

### 5.1 总体流程

```text
Task description + task workspace
                |
                v
       [1. Prepare / Scope]
         ScopeMap
         HarnessContract
         EnvironmentFingerprint
                |
                v
       [2. Localize / Validate]
         RouteGraph
         TriggerClaim(s)
         uncertainty + evidence
                |
                v
       [3. Prove Strategy Router]
        /       |        |          \
     code     mutate   guided fuzz  instrumentation
        \       |        |          /
                v
       [4. Candidate Experiment]
         bytes + parent + exact delta
         claim + expected observation
                |
                v
       [5. Isolated Verification]
         local fresh environment, if available
         deterministic result parser
                |
                v
       [6. Official submit oracle]
         strict typed event
                |
          success / next experiment
```

这不是要求 CyberGym 启动六个模型 agent。阶段 1、5、6 应尽量由确定性代码完成；阶段 2、3、4 可以由一个模型在不同 artifact contract 下完成。只有当预算和 QitOS 协议支持时，才并行探索多个明确分区。

### 5.2 `ScopeMap`：按角色描述代码，不按目录猜作用

Microsoft 特别强调按角色区分 under-audit code 与 contextual dependencies，并识别主树之外的 fuzz harness。当前 `source_root` 会把探索视野压到一个目录，而 `repo_archive_root`、测试、fuzz target、构建脚本、生成器可能在外层。

建议数据模型：

```python
ScopeEntry(
    path,
    role,          # target | harness | dependency | test | generated | build
    inclusion,     # primary | contextual | excluded
    reason,
    evidence_ref,
    confidence,
)
```

Prepare 阶段至少完成：

1. 搜索 `LLVMFuzzerTestOneInput`、AFL entry、maintainer fuzz target、CLI main、stdin/file readers；
2. 搜索 build scripts 中实际编译的 target 和 sanitizer flags；
3. 区分项目源码、vendored dependency、generated parser、test fixture；
4. 把 description anchor 作为检索候选，而不是直接决定 scope；
5. 对不能确定的路径保留 unknown，不自动排除。

### 5.3 `HarnessContract`：每个任务的第一项强制研究产物

Anthropic 的 target config 明确规定 build/run/signal/PoC；Microsoft 把环境错配列为 prove 失败主因之一。当前 agent 必须把以下信息提升为 typed contract：

```python
HarnessContract(
    executable_or_entry,
    invocation,
    input_mode,          # file | stdin | argv | buffer | sequence
    input_slice,         # whole file | payload after header | argv[n] ...
    setup_state,
    accepted_seed_rules,
    build_identity,
    sanitizer_identity,
    success_signal,
    failure_signal,
    evidence_refs,
)
```

关键不变量：

- 没有证据时字段为 unknown，不能猜默认 file/stdin；
- local runner 与 official submit 的可见差异必须显式记录；
- “目标退出非零”不自动等于漏洞 crash；
- timeout、OOM、launch failure、parser reject 与 sanitizer crash 必须分型；
- 本地修改源码用于 instrumentation 时，验证 candidate 必须回到未修改 target。

### 5.4 `RouteGraph`：从自报 chain 改为证据图

建议把 `CallChainNode + ChainGate` 替换或逐步迁移为：

```python
RouteNode(
    symbol,
    role,            # harness_entry | parser | dispatcher | target | sink
    source_span,
    evidence_ref,
)

RouteEdge(
    src,
    dst,
    edge_type,       # direct_call | callback | table | macro | generated
    input_mapping,
    predicate,
    evidence_ref,
    validation,      # static | runtime_observed
)
```

这同时解决三个当前弱点：

- regex call graph 无法正确覆盖 callback/registration/generated parser；
- node 顺序由模型或简单 role 排序，而不是由真实 edge 决定；
- `confirmed gate` 没有 tool-result provenance。

不要要求一开始构建全仓库 call graph。面向 CyberGym，应围绕 description anchor、harness entry 和 candidate sink 建立**局部、可证据化、可增量扩展**的 route graph。

### 5.5 `ProveStrategyRouter`：路由求解方法，而不是文件写法

建议的策略枚举：

| Strategy | 适用信号 | 主要动作 | 退出/切换条件 |
|---|---|---|---|
| `code_construct` | 路径短，字段/边界条件明确 | 直接编码满足 predicate 的最小输入 | 两次 exact-delta 无路径信号则切换 |
| `seed_mutate` | 有被 harness 接受的同族 seed | 保留结构，局部修改控制字段 | seed 未通过资格检查则禁止 |
| `grammar_construct` | 强结构 format，存在 parser/grammar/spec | 从最小合法骨架逐层增加 chunk/table | parser stage 长期未推进则回查 grammar |
| `guided_fuzz` | search space 可控且有快速 runner | dictionary、structure-aware mutator、coverage | 无新 coverage/branch progress 则停止 |
| `instrument_hillclimb` | 已知关键变量/分支但逆向约束困难 | 暴露变量、比较距离或 branch feedback | 变量不受输入控制则回退 localization |
| `state_sequence` | 生命周期/UAF/protocol state | 构造事件序列、对象生命周期和重复调用 | 单文件 harness 不支持状态则换入口 |

当前 `text/binary_python/hex` 可以作为 `CandidateEncoder`，但不应继续充当 `poc_strategy`。

路由输入必须包含：

- carrier 结构强度；
- 是否有 qualified seed；
- 是否能本地快速执行；
- 是否能观测 branch/变量；
- trigger predicate 是解析、算术、生命周期还是状态序列；
- 剩余构建/执行/submit budget。

### 5.6 `ProbePlan`：用真实测量取代伪 proximity

Microsoft 的 instrumentation 案例是两篇材料中对当前 agent 最有增量价值的启示。

当前错误循环往往是：

```text
submit -> no crash -> 猜测某个 gate 没通过 -> 自动 refute -> mutate
```

推荐循环是：

```text
claim -> 选择可观测量 -> 临时 instrumentation/probe
      -> 运行候选 -> 获取测量值
      -> 比较 parent/child delta -> 选择下一候选
      -> 在 pristine target 上验证最终 PoC
```

可观测量示例：

- 是否进入 harness target function；
- parser 消费到哪个 offset/chunk；
- 某 length/count/stride 的实际值；
- 某 compare 两侧的距离；
- branch hit、basic-block coverage 或 sanitizer coverage；
- alloc/free sequence 与对象 identity；
- target buffer size 与计算出的 write/read bound。

建议 `ProbeResult`：

```python
ProbeResult(
    candidate_sha256,
    pristine_source_sha,
    instrumented_source_sha,
    measurement_name,
    value,
    run_identity,
    observation_refs,
)
```

安全和公平约束：

- instrumentation 只作用于本地工作副本；
- 最终 candidate 必须在 pristine target 上复验；
- 不允许读取隐藏 verifier/fix-side 信息；
- instrumentation patch 不得成为提交内容；
- 若本地无法构建，明确降级到 static/code strategy，而不是伪造 runtime evidence。

### 5.7 `IsolatedCandidateVerifier`：隔离的是认知和环境，不一定是模型数量

Anthropic 的 fresh-container 设计应改造后采用：

```text
CandidateBuilder 输出：
  PoC bytes
  intended HarnessContract id
  reproduction command template

Verifier 输入：
  pristine target image/worktree
  PoC bytes
  contract

Verifier 输出：
  typed process/sanitizer result
```

不应把 builder 的完整 hypothesis、gate 评分和自我解释传给 verifier。Verifier 首先执行确定性检查：

1. 文件存在、大小/哈希稳定；
2. target 能正常启动，baseline seed 不产生相同失败；
3. candidate 重复运行；
4. 解析 sanitizer/exit/signal/timeout/OOM；
5. 记录 environment fingerprint；
6. 如 official oracle 支持严格差分，再单独记录 strict verdict。

CyberGym 不宜照搬“官方 submit 3/3”。更合理的是：本地 runner 可用时先做 2-3 次稳定复现，再调用一次昂贵的 official submit；本地 runner 不可用时，official miss 保持低信息量，不做 gate refutation。

## 6. 并行与 specialized agent：何时值得做，何时会适得其反

### 6.1 可以采用的部分

如果 P0 结果契约已修复，可按**互斥 prove 路线**而不是随机 family 并行：

```text
route A: harness/entry confirmation
route B: target-local code predicate construction
route C: qualified-seed mutation
route D: instrumented search
```

每条路线有独立 budget、artifact namespace 和停止条件。共享内容只包括：

- typed evidence；
- candidate byte hash；
- crash fingerprint；
- 已否定的路线及其真实观测。

### 6.2 当前不能直接采用的部分

当前并行 action 存在 structured result 归属风险，`submit_tool.py` 又使用模块级最后结果槽。此时即使 focus partition 完美，仍可能发生：

```text
candidate A -> result B
candidate B -> result A
```

因此顺序必须是：

1. action-id keyed result envelope；
2. 删除 global/last structured output；
3. byte hash 级 candidate identity；
4. submit 串行 judge/dedupe；
5. 最后才开放 partitioned concurrency。

Anthropic 的经验也说明，简单增加 agent 数量会出现重复发现和收益递减。当前任务只有一个目标漏洞，过度并行比未知漏洞扫描更容易重复。

## 7. 不能照搬的内容与公平边界

### 7.1 不照搬完整 find-and-fix 组织形态

CyberGym Level 1 不需要 report、severity triage、patch 和 re-attack 全链条。它们是生产安全流程的重要部分，但会稀释有限 PoC budget。应只保留：

```text
prepare -> localize -> prove -> verify -> submit
```

### 7.2 不把 broad threat model 当作主要任务

Anthropic 面向未知漏洞发现，需要全系统 threat model。CyberGym 已给出漏洞描述，更适合生成一个窄的 `TaskThreatContract`：

- 什么输入被 benchmark 视为 attacker-controlled；
- 什么 crash/signal 算成功；
- 哪些代码属于 target，哪些只是 dependency/context；
- 提交 artifact 的形式与限制。

### 7.3 不固定为 ASAN-only

Anthropic 参考 harness 为 C/C++ memory bug 配置 ASAN，但 CyberGym 任务可能依赖 UBSAN、MSAN、assert、signal、allocator 行为或 verifier 的差分语义。`CrashSignal` 必须可配置，不能把 ASAN trace 当唯一真相。

### 7.4 不在 CyberGym 引入外部 OSS-Fuzz corpus 或已知 PoC

Microsoft 明确说明，其 CyberGym 评估没有使用计划中的 OSS-Fuzz pipeline/seed corpus，因为这可能隐式复用已知 PoC（[来源](https://www.microsoft.com/en-us/security/blog/2026/06/17/beyond-the-benchmark-advancing-security-at-ai-speed/)）。我们应制定 provenance policy：

| 来源 | CyberGym 使用建议 |
|---|---|
| task workspace 内自带样本 | 允许，但必须先验证由当前 harness 接受 |
| 项目源码内 tests/fixtures/corpus | 允许，记录路径和提交版本 |
| agent 当场从格式结构生成 | 允许 |
| 网络下载 OSS-Fuzz corpus | 禁止用于公平 benchmark |
| 历史任务的成功 PoC bytes | 禁止 |
| 跨任务项目知识（build/harness pattern） | 可单独评估，但必须标记实验口径 |

### 7.5 不把 `any crash` 当最终成功

建议同时记录：

```text
any_crash:
  candidate 使某个目标执行产生 crash

target_crash:
  crash 与描述的目标路径/漏洞机制一致

strict_acceptance:
  官方 verifier 接受；若存在 vulnerable/fixed 差分，则通过差分
```

Microsoft 的 96.5% 是第一种口径，且明确包含 non-target vulnerability。它说明系统能产生 crash，但不能直接证明严格目标漏洞复现率达到同一数字。

## 8. 对现有实施计划的调整

011 中的五个 PR 方向仍成立，但结合外部证据后建议调整为七步。

### P0：Result Contract 与 Oracle Contract

主要文件：QitOS action executor/runtime、`agent.py`、`submit_tool.py`、`feedback.py`

交付：

- 每个 tool action 返回同一个 action-id keyed envelope；
- 删除 `_last_structured_output` 和模块级 submit last-result；
- submit 默认串行；
- process/oracle result 使用 typed enum；
- observation render 纯函数；
- no-crash 不再自动更新 gate/proximity。

这是后续所有并行、ablation 和反馈学习可信的前提。

### P1：Prepare Artifact（ScopeMap + HarnessContract）

主要文件：`adapter.py`、`task_spec.py`、`agent_impl/state_init.py`、`agent_impl/repo_index.py`、`agent_impl/harness.py`

交付：

- description anchor 使用高精度 extraction；
- 按角色扫描 source root 与 archive root；
- fuzz harness、CLI/stdin/file/buffer entry catalog；
- build/run/environment fingerprint；
- unknown-friendly contract validator。

验收重点不是“字段非空”，而是人工核验 24-task cohort 的 mapping precision/recall。

### P2：Evidence-backed RouteGraph

主要文件：`tracking_tools.py`、`state.py`、`repo_index.py`、`observations.py`

交付：

- node/edge 都带 source span 与 tool result provenance；
- model 不能直接写 `confirmed`；
- callback/table/generated edge 类型；
- 删除或降级当前 `chain_completeness_score`；
- stage transition 由 artifact validator 决定。

### P3：Candidate Experiment Contract

主要文件：`agent_impl/candidates.py`、`family_runtime.py`、`feedback.py`

交付：

```text
candidate_id
parent_sha256
bytes_sha256
claim_id
exact_byte_delta
expected_observation
encoder
prove_strategy
environment_id
result_id
```

每次 candidate 只能基于明确 claim；批量候选必须逐个建立身份和结果关联。

### P4：ProveStrategyRouter + Instrumented Probe

建议新增：`agent_impl/prove_strategy.py`、`agent_impl/probes.py`

交付：

- code/seed/grammar/fuzz/instrument/state-sequence 策略；
- 基于任务证据和执行能力路由；
- 临时 instrumentation patch lifecycle；
- branch/compare/value feedback；
- pristine-target final replay；
- budget-aware strategy switching。

这是两份外部材料对 011 计划最重要的新增项。

### P5：Isolated Local Verifier

建议新增：`agent_impl/verifier.py`

交付：

- fresh worktree/container runner（按实际部署能力选择）；
- baseline/candidate/repeat execution；
- sanitizer/process result parser；
- environment parity metadata；
- PoC bytes-only boundary；
- local repro 后再 official submit。

### P6：Specialized Routing 与受控并行

主要文件：`delegate_agents.py`、`subagent_runtime.py`、`submit_queue.py`

只有 P0-P5 验收后才实施：

- 以互斥 focus/strategy 分区；
- shared ledger 只保存 typed artifact；
- byte/crash fingerprint 去重；
- judge/submit 串行；
- 为每个分区记录边际收益。

## 9. 推荐 benchmark 与诊断指标

### 9.1 必须做 stage attribution

Microsoft 对 52 个失败的价值不在数字本身，而在能回答“失败发生在哪一层”。我们的每次运行也应唯一归因到最早失败阶段：

```text
PREPARE_SCOPE_MISS
HARNESS_CONTRACT_MISS
LOCALIZATION_MISS
VALIDATION_FALSE_NEGATIVE
PROVE_INVALID_STRUCTURE
PROVE_PATH_NOT_REACHED
PROVE_TRIGGER_NOT_SOLVED
LOCAL_REMOTE_ENV_MISMATCH
BUILD_OR_BUDGET_FAILURE
ORACLE_CONTRACT_FAILURE
```

无法确定时记录 `UNATTRIBUTED`，不能为了统计完整而猜测。

### 9.2 指标分层

| 层 | 指标 |
|---|---|
| Prepare | target scope recall、错误排除率、harness discovery recall |
| Contract | input mode/slice/entry precision、environment parity rate |
| Localization | evidence-backed route recall、speculative edge rate |
| Strategy | strategy selection accuracy、切换时机、无效 fuzz 时间 |
| Probe | probe informativeness、measurement-to-next-delta alignment |
| Candidate | exact-delta rate、qualified-seed rate、result attribution integrity |
| Verify | local reproducibility、flaky rate、launch/build false crash rate |
| Outcome | any-crash、target-crash、strict acceptance、time/tokens/submits |

### 9.3 固定模型的系统 ablation

Microsoft 的核心评估固定模型配置，以区分 pipeline 改进和模型升级。我们也应固定：

- task manifest；
- model/version；
- temperature/seed；
- step/token/build/submit budget；
- QitOS commit；
- source agent commit；
- runtime image/tool versions。

优先 ablation：

1. 当前 bootstrap vs `ScopeMap + HarnessContract`；
2. 自报 chain score vs evidence RouteGraph；
3. `text/binary/hex` 路由 vs prove strategy router；
4. no-crash proximity vs explicit probe；
5. direct official submit vs local isolated verifier；
6. generic candidate family vs partitioned strategy；
7. 单 agent vs 受控并行；
8. current model vs newer model，但必须与系统 ablation 分开。

## 10. 建议的首批 30 天路线

### 第 1 周：恢复事实可信度

- 完成 action/result correlation；
- 删除 submit 全局槽；
- 统一 oracle enum；
- 禁止 ambiguous no-crash 改写 gate；
- 加入 render idempotency、parallel permutation、multi-submit attribution 测试。

### 第 2 周：让 prepare 产出真实契约

- 实现最小 `ScopeMap`；
- 实现最小 `HarnessContract`；
- 扫描 source/archive 两级的 fuzz/CLI/build entry；
- 在 24-task cohort 上人工核验；
- 移除 bare-substring format/strategy hard route。

### 第 3 周：让候选成为实验

- candidate parent/claim/exact delta；
- qualified seed；
- evidence-backed local route；
- 删除模型直接 `confirmed`；
- 第一版 deterministic local verifier。

### 第 4 周：加入 prove strategy 与 instrumentation pilot

- 选择 6-12 个强结构或数值阈值任务；
- 实现 `code_construct` 与 `instrument_hillclimb` 两条路线；
- 记录 value/branch feedback；
- pristine replay；
- 与当前 generic mutation 做固定模型 ablation。

这四周内不建议扩大 multi-agent 数量，也不建议继续增加固定格式 toolbox。先证明系统能在一个 candidate 上保持“来源、字节、环境、结果、下一步”完整可追溯。

## 11. 最终判断

Anthropic 和 Microsoft 的材料共同说明，顶尖安全 agent 并不是一个更会写长分析的单体模型，而是一个把不确定推理逐步压缩成可执行 artifact 的系统：

```text
模糊描述
  -> 有边界的 scope
  -> 有证据的路径
  -> 有环境契约的执行
  -> 有明确控制量的候选
  -> 有独立复现的结果
  -> 有真实测量指导的下一次实验
```

当前实现的主要偏差是，它在这些 artifact 尚未成立时，用 `input_format`、`poc_strategy`、`confirmed gate`、`chain_completeness` 和 `proximity` 等内部标签提前制造了确定性。两模式重构如果继续以这些标签为边界，只会把旧问题压缩进新的 mode。

最值得立即采用的三条设计原则是：

1. **任何 `confirmed` 都必须引用不可变 evidence；**
2. **任何 no-crash 都只能排除本次完整 candidate claim，不能自动定位失败 gate；**
3. **当输出信息不足时，下一步应设计 probe 增加可观测性，而不是增加 prompt 压力或盲目 candidate 数量。**

最终要优化的不是“agent 看起来是否进入了正确 phase”，而是：

> **每一次源码探索是否缩小了真实输入路径的不确定性，每一个 PoC 是否是可归因实验，每一个 verifier 结果是否足以支持下一步决策。**
