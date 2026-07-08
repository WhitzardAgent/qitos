# v14-r2 Context 与动态工具审计

审计时间：2026-07-06

审计对象：

- 16 条已完成 crash trace：`remote_traces_v14_gdb_new_r2_149_20260706_1123_crash16`
- 3-task schema smoke：`remote_traces_v14_gdb_schema_smoke_149_20260706_1145`
- 当前实现：`/Users/morinop/Desktop/traj_analyzer/cybergym_agent`
- 安全专家实现：`/Users/morinop/Downloads/cybergym_agent`，重点提交 `3064b97`、`aec8fb2`

## 结论

当前 observation 的主要问题不是信息不足，而是缺少严格的信息选择和决策层次：低价值事实、错误 harness 先验、重复条件、机器生成的泛化 gap 与真正 source-backed evidence 被放在近似相同的视觉权重上。对一个中级 CS 工程师而言，这会增加判断成本，并诱导模型修复错误的 blocker。

动态 schema 闭环已经修通，但两个动态工具的证据质量尚未达到安全专家调试工具的水平。smoke 中：

- `run_candidate` 返回 `input_rejected`，实际却是 `exit_code=null`、stdout/stderr 为空、耗时约 14 秒；代码把 Docker command error/timeout 丢失后误分类为 input rejection。
- `probe_runtime_frontier` 没有命中任何 probe，GDB 脚本因 source path/breakpoint command 失败，但 observation 仍展示 `first_unreached=harness_entry`，形成不应存在的路径诊断。
- GDB 结果真正有用的信息是“probe setup 失败”，不是“harness 未到达”。当前状态和 renderer 混淆了 infrastructure failure、breakpoint resolution failure、target execution result 与 path frontier。

因此，当前工具已经“会被调用”，但还不能稳定提供可信的动态诊断。若不修证据契约，强制调用反而可能把错误结论提升为 hard block。

## 审计覆盖

16 条轨迹各完整阅读了最后一份 observation；另外完整阅读了 smoke 中具有内容的两条 observation，并逐步检查 `run_candidate → probe_runtime_frontier → observation`。

代表 observation 统计：

| 指标 | 结果 |
|---|---:|
| 完整阅读的 crash observation | 16 / 16 |
| `Active objective: (none)` | 12 / 16 |
| `No mechanism graph yet` | 16 / 16 |
| runtime 一直显示 `rediscovery_pending` | 16 / 16 |
| 尚无提交 | 12 / 16 |
| Supplementary 行数 | 97，平均 6.1/条 |
| Supplementary `read_fact` | 56 |
| Supplementary bookkeeping feedback | 23 |
| Supplementary 自动 Suggested | 12 |
| Likely 中 `literal_text` 搜索命中 | 37 |
| 未确认 harness | 15 / 16 |

整个 16-trace 快照共有 251 份 observation：

| 信号 | observation 数 |
|---|---:|
| Active objective 为空 | 192 |
| No mechanism graph | 175 |
| rediscovery_pending | 251 |
| 包含 read_fact | 190 |
| 包含 literal_text refs | 116 |
| 包含 Suggested constraint | 69 |
| 包含 feedback_file | 59 |

## 逐轨迹观察

| task | 主要 context 问题 |
|---|---|
| arvo:52243 | Likely harness 是无关的 demangler fuzzer，而真实 path 是 `fuzz_objdump`; Supplementary 含浅层声明、重复 prototype 和无关 hazard；feedback bookkeeping 占据大量位置。 |
| arvo:8903 | 多个 read_fact 只是文件首行、结束花括号或 debug printf；自动分析把 OOM 归因到 `name_idx`，但 evidence 不足；objective 与 sink 在运行中发生漂移。 |
| arvo:29266 | Likely harness 错指 BMP loader，而真实 harness 是 FuzzJs；Active objective 为空；Likely 的 sanitizer literal refs 与当前决策没有关系。 |
| arvo:43599 | 描述是 CFF/Ghostscript，Likely harness 却是 libpng；dictionary/offset literal refs 指向 AES/gdbflags；Required Conditions 为空却出现 `SUBMIT NOW`。 |
| arvo:55933 | 21 step 后仍无 path、无 conditions、无 objective；Supplementary 只有浅层文件读取记录；wrong demangler harness 持续可见。 |
| arvo:1348 | 多个 `avctx->width/height` literal refs 来自不相关 decoder；Code context 聚焦 harness 的 `error` helper，而不是 tiertex parser/sink；objective 为空。 |
| arvo:14703 | KAr archive 被标成 `Input: zip`；Likely 罗列多个同名 openArchive；路径包含 fuzz entry、示例 main 等混合入口但没有说明哪条是实际 harness path。 |
| arvo:17006 | 四次 no-crash 只展示三行且重复同一路径；path 只有 entry，核心 sink mapping 缺失；Supplementary 仍展示大量非语义 read_fact。 |
| arvo:17607 | Vulnerability Path 重复 `draw/layoutText/generateLineAppearance` 节点；Suggested 的 `text/uChar is uninitialized` 与真正 charCount/width 机制混淆；feedback 元数据与有效条件竞争注意力。 |
| arvo:10999 | bootstrap harness 错指 config fuzzer，真实是 objects fuzzer；author literal refs 来自 HTTP parser；Code context 指向 examples/tag 而非 active sink。 |
| arvo:10865 | bootstrap harness 错指 expr parser，真实是 odp target；sink 与 possible sink 重复；conditions 已足够时其余导航噪声仍保留。 |
| arvo:18140 | `input_buffer == data`、`input_offset_increment == sizeof(uint32_t)` 是普通 harness dataflow，却被自动 recipe gap 提升成禁止提交的 hard block。 |
| arvo:10628 | `[idx] < 0 || [idx] >= len` gap 已被完整 source-backed 条件解释，却仍阻止提交；Required Conditions 内存在高度重复的 bounds 条件。 |
| arvo:11007 | author literal refs 与 active commit parser 无关；bootstrap harness 错；两个 sink 名称/位置关系不清；大量 util/signature 的浅层 read_fact 无决策价值。 |
| arvo:11167 | wrong config harness；自动 shift-count hazard 与 NUL termination bug 无关；只有“首字节是八进制”不足以表达完整 carrier。 |
| arvo:13466 | Capstone 原始指令字节被误标为 `Input: video`；read_fact 多为孤立源码行；无 objective、无 sink path、无具体输入映射。 |

## Context 问题归因

### P0：事实选择器把“观察过”当成“有价值”

`sections.py` 直接取最近 6 条 `durable_code_facts` 和最近 6 条 feedback facts，没有语义质量门槛：

- `file -> 1 /*`
- 单独的 `{` / `}`
- prototype/changelog
- debug printf
- 只说明完整结果保存在某路径的 bookkeeping

这些记录只能证明工具读过某处，不能帮助当前决策。应在写入 durable facts 时先生成结构化 claim，而不是在 renderer 尾部截断原始命中行。

### P0：harness 先验与已读代码冲突时没有撤销

15/16 代表 observation 仍显示未确认 bootstrap harness，多条明显错误。即使 agent 已读到真实 harness，旧候选仍在 Likely 或 Supplementary 中持续出现。

需要：

- selected/confirmed harness 成为唯一主展示；
- 读到不同 harness 后，bootstrap 候选降级为 rejected/alternative；
- 不把“仓库中第一个 LLVMFuzzerTestOneInput”视为任务 harness；
- harness path 必须结合 staged binary 名、task build metadata 或 source-backed target mapping。

### P0：机器生成的 generic gap 被错误提升为 hard block

`input_buffer == data`、`[idx] out of range`、`text is uninitialized` 等自动表达式并不一定需要额外 input mapping。当前 recipe gap 没有检查：

- 是否已被 source-backed gate 覆盖；
- 是否只是 tautology/普通参数传递；
- 是否能对应到可修改的 offset/field；
- 是否比现有 candidate 更具体。

只有“可命名输入字段 + 缺失 offset/encoding/value constraint”的 gap 才能进入 hard Next Action。泛化 dataflow 只能作为低优先级研究提示。

### P1：同一信息跨 section 重复

常见重复：

- Mission crash prior → Likely crash prior；
- Vulnerability Path numerical → Required Conditions → Numerical constraints；
- runtime feedback → Supplementary bookkeeping → Experiments → Rejected；
- sink 在 Confirmed、Path、Required objective 中重复但角色不一致。

每个事实应有单一主位置：

- 任务事实放 Mission；
- 当前判断和置信度放 Assessment；
- 因果链只放 Path；
- 可操作输入约束只放 Required Conditions；
- 运行结果只放 Experiments；
- 下一动作只放 Next Action。

### P1：空状态长期占据版面

`No mechanism graph yet` 在 16/16 代表 observation 中出现，`Active objective: none` 为 12/16。空槽位应转化为具体 blocker 或完全隐藏，而不是每轮重复模板文本。

当 sink 已确认时应立即合成 objective，不应等 no-trigger 后才补。objective 至少包含 target、mechanism prior、observable verdict 和当前 candidate family。

### P1：信息被字符截断后既不可读也不可恢复

大量行以 `ca`、`stil`、`no inp` 等半句结束。固定字符截断破坏语义，并让模型无法判断原结论。应按结构字段限制数量，避免按任意字符切断；超长 evidence 用短 claim + `evidence_ref`。

## 推荐的 observation 结构

面向中级 CS 工程师，建议固定为六段，但重写每段的信息预算：

### 1. Mission（3–4 行）

- 完整但紧凑的漏洞描述；
- bug/crash prior 及来源；
- 实际输入契约和 selected harness；
- benchmark success oracle。

### 2. Decision Snapshot

- active objective；
- 当前最可信机制假设；
- latest verdict；
- 单一 blocker；
- runtime capability 只在首次、发生变化或当前动作需要时显示。

不再默认展示 Supplementary。原始 refs 放到 evidence index，只有被当前 objective/path/condition 引用的 claim 才提升到正文。

### 3. Causal Path

- 去重后的 `harness → parser → dispatch → pre-sink → sink`；
- 每个节点最多一个 source location；
- 只展示决定控制流的 gate；
- 明确 `confirmed / inferred / contradicted`，禁止重复节点和混合多个入口而不解释。

### 4. Input Contract

用表格表达：

```text
field/offset | encoding | required value/range | controls | evidence | status
```

只收录可由 PoC 控制的条件。普通参数传递、静态分析泛化式和已被覆盖的 gap 不进入 hard block。

### 5. Experiments

只保留最近 3–5 个不同 candidate family/digest：

- candidate/delta；
- submit verdict；
- local execution result；
- frontier evidence；
- 本轮学到什么；
- 下一变更轴。

不得把 `input_rejected` 自动解释为 parser rejection，除非有 exit/stderr 或 probe hit 证据。

### 6. Next Action

- 一个动作；
- 明确 target；
- 为什么它能消除当前 blocker；
- stop condition；
- 禁止的无效重复动作。

## 动态工具与安全专家实现对照

| 维度 | 当前实现 | 安全专家实现 |
|---|---|---|
| 快速执行 | `run_candidate`，typed profile/evidence ledger | `run`，直接解析 `/out`、输出 exit/crash/raw tail |
| GDB | 自动生成 source-backed frontier probes | `gdb_debug` 允许模型提供 batch GDB commands |
| 调度 | feedback arbitration：run → probe → repair | `pending_reproduction`：NO_TRIGGER 后只开放 gdb_debug |
| 安全性 | 不允许 raw GDB commands，风险更低 | allowlist/清洗部分输入，但模型可控制 break/print/x 等命令 |
| 输出 | 结构化 frontier，理论上 token 经济 | 原始 GDB tail，信息丰富但可能很吵 |
| 失败恢复 | 重新仲裁；设计更完整 | gdb error 后 latch unavailable，简单可靠 |
| 当前实跑有效性 | schema 已闭环，但证据发生误分类和 probe setup failure | 更灵活地适配符号/路径，但缺少结构化 frontier 与严格 evidence semantics |

不建议直接替换成专家版，也不建议保留当前固定 frontier 不变。推荐混合设计：保留当前 typed state/evidence、安全边界和自动调度，引入专家版的 target autodetection、可解释 invocation、symbol-first GDB 和受限调试命令能力。

## 动态工具的具体缺陷

### P0：Docker command error 被误分类为 input_rejected

smoke 原始返回：

```text
outcome=input_rejected
exit_code=null
stdout_tail=""
stderr_tail=""
elapsed_ms≈14032
```

`DockerCommandCapability.run()` 在 timeout/exception 时返回 `{status:error,error:...}`，没有 `returncode`。`_run_with_qitos_env()` 忽略 `status/error`，得到 `rc=None,timed_out=False`；`classify_execution()` 最后把未知非零状态归为 `input_rejected`。

必须改为：

- command capability `status=error` → `environment_error` 或明确 `runner_timeout`；
- `returncode=None` 且无明确正常完成证据，绝不能判定 input rejection；
- 返回实际 command、binary、mode、cwd、argv/stdin wiring、timeout、runner error；
- `input_rejected` 需要 exit code + usage/parser stderr pattern 作为证据。

### P0：GDB script setup failure 被包装成 frontier

smoke 中 GDB 报 source file 不存在，随后裸 `commands` 没有关联 breakpoint，脚本在 target run 前失败。当前 parser 仍根据“所有 probe 均未 hit”填入 `first_unreached=harness_entry`。

必须拆分：

```text
probe_setup_status
resolved_probes[]
unresolved_probes[]
target_started
target_exit_kind
hit_probes[]
frontier_status
infrastructure_error
```

只有 `target_started=true` 且 probe setup 成功时，才能计算 last-hit/first-unreached。capability/script error 时 frontier 字段必须为空，不能产生 path negative evidence。

### P0：breakpoint 解析策略不适配真实 debug symbols

当前 `_breakpoint_target()` 优先 `file:line`，而 staged binary 的编译路径可能不同。应：

1. GDB preflight 查询 `info functions <symbol>` / `info sources`；
2. 优先 qualified function symbol；
3. source path 做 basename/suffix remap；
4. `set breakpoint pending on`；
5. 只为成功创建的 breakpoint 绑定 commands，使用明确 breakpoint number；
6. 无法解析的 probe 记录为 unresolved，不让整个脚本终止。

### P1：run_candidate 返回结果缺少决策解释

最低输出契约应包含：

- `execution_status=completed|timeout|runner_error`；
- `binary_path/mode/argv/cwd/library_path`；
- `exit_code/signal/sanitizer/top_frame`；
- `classification` 与 `classification_reason`；
- stdout/stderr 非空摘要；
- `candidate_digest/evidence_ref`；
- 若 rejected，明确依据是 usage、format error、open failure 还是未知非零退出。

### P1：probe 只能回答位置，不能回答条件值

即使 frontier 正常，固定 hit probe 只能说明经过哪些点，无法检查 sink guard、关键局部变量或 parser return。安全专家版允许 `print/info locals/x`，信息量更高。

建议提供受限的第二级工具参数，而不是完全开放 raw script：

- `inspect_symbols=[...]`
- `inspect_expressions=[...]`，仅允许简单变量/字段表达式；
- `breakpoint_symbols=[...]`，必须来自 source-backed state；
- 内部生成 GDB 命令并做 allowlist；
- 输出结构化 variable observations，不直接灌 6000 字符 raw tail。

## 修复优先级

### 第一批：先保证动态证据可信

1. 修复 Docker command error/timeout 传播和 run classification。
2. 修复 GDB breakpoint preflight、script generation 与 setup error 分类。
3. capability error 不得生成 frontier/negative path evidence。
4. 为两个工具增加高信息量、低噪声 renderer。
5. 用 smoke 验收真实 hit，而不仅是 tool call 出现。

### 第二批：重做 context evidence selector

1. 移除默认 Supplementary，改成 objective-scoped Evidence。
2. durable fact 写入时结构化、去重、质量过滤。
3. harness 候选冲突消解。
4. path/node/condition 去重。
5. generic recipe gap 不得 hard block。
6. sink confirmed 后立即合成 objective。

### 第三批：混合式专家调试能力

在自动 frontier 之外增加受限 symbol/variable inspection；保留当前 typed ledger，不直接照搬专家版 raw GDB output。

## 验收标准

- 任何 `input_rejected` 都必须有非空依据：exit code + stderr/usage/parse signature。
- command timeout/error 不得写成 candidate negative evidence。
- GDB capability/setup error 时 `last_hit`、`first_unreached` 均为空。
- 至少一个 smoke 真实记录 `target_started=true` 和一个 resolved/hit probe。
- observation 中不再出现 raw `feedback_file/feedback_analysis` bookkeeping。
- 单份代表 observation 中，未被 active objective/path/condition 引用的 read facts 和 literal refs 为 0。
- selected harness 与 rendered path entry 一致；冲突候选进入 rejected/alternative。
- generic/tautological recipe gaps 不得覆盖 ready candidate 或 submit action。
- 每个事实只在一个主 section 展示；Next Action 始终只有一个可执行 blocker。

