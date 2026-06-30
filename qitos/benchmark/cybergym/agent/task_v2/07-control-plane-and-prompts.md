# 07 — 收敛两模式控制面并重写 Prompt/Tool Policy

优先级：P1  
依赖：[04](04-evidence-backed-chain.md)、[06](06-oracle-feedback-and-stop.md)  
禁止：新 phase 层、硬 read deadline、本地 fuzz/debug/run 指令

## 最新再审计状态

状态：**两模式骨架已出现，但当前语义与本任务目标相反**。

当前新增文件可直接修改，不要再创建另一套 mode prompt：

- `agent_prompts/mode/chain_construction.md`
- `agent_prompts/mode/poc_iteration.md`

必须优先移除：

- completeness < 0.3 时硬阻止 WRITE/BASH/submit；
- chain prompt 的“Do NOT construct a full PoC”；
- step 12 同时强制 mode 和 submit pressure；
- proximity 驱动返回 chain mode；
- poc prompt 的 corpus `ALWAYS`；
- “each miss tells which gate is wrong”；
- public vul crash 后继续 discriminant/precision refinement；
- render 中 suggestion prune/count/promotion mutation。

可保留：两个 mode 名称、prompt resource 加载方式、Chain Work Order 展示位置。但 work order 必须基于 typed artifact gap，不能要求每 N 步填 node/gate。

## 目标

保留当前工作树正在形成的 `chain_construction / poc_iteration` 两模式，但让它们成为由 artifact 缺口派生的软指导，而不是新的硬状态机。删除 phase/control/proximity/candidate-required 的互相打架。

## 必须复用

- `agent_impl/phase.py::cybergym_phase_engine()`
- `agent_impl/validation.py::_derive_control_mode()`
- `agent.py::prepare()`、`build_system_prompt()`、`reduce()`
- `agent_impl/observations.py`
- `agent_prompts/system/*`
- `agent_prompts/phase/*`
- 现有工具，不新增 tool

必须在用户当前未提交的两模式改动之上小步修改，禁止拿旧四 phase 文件覆盖。

## 目标控制模型

持久状态只保留：

```text
agent_mode = chain_construction | poc_iteration
```

派生而非独立真相：

- `current_phase`：仅 legacy display；
- `control_mode`：每步根据 artifact/pending action 计算，不作为长期决策源；
- `runtime_stage`：停止参与工具门控，逐步废弃；
- `candidate_required`：只生成一句软压力，不过滤 READ/GREP；
- `proximity_score`：不参与任何 transition。

## Mode 派生规则

### chain_construction

当不存在可构造 claim，且没有 ready candidate：

- 优先补 `HarnessContract` 未知字段；
- 找到 target definition/callers；
- 形成 supported TriggerClaim；
- 允许随时用 BASH/WRITE 构造早期候选，不禁止。

### poc_iteration

满足任一：

- 有 ready candidate；
- 已提交至少一个 candidate；
- 有 supported claim + 可用 encoder/seed。

该模式仍允许 targeted READ/GREP/CallsiteSearch。官方 miss 后默认留在 poc_iteration，除非 agent 明确废弃当前 claim；不因 fake proximity 自动退回。

### 删除固定 deadline 的真值作用

step 8/10/12 可以显示“应尽快形成首个 candidate”的提醒，但不能：

- 自动把 chain 标完整；
- 阻止 READ；
- 强制提交不存在/空洞的 candidate；
- 切换后锁死工具集。

## Action gating

仅保留安全/事实 gate：

- path 必须在 workspace；
- ready candidate 文件必须存在；
- duplicate bytes 不得提交；
- pending required ledger action（若继续保留）只能短暂阻止冲突写入；
- public crash 后不再执行 action。

删除或降级：

- read budget hard block；
- candidate_ready 时禁止所有非 submit；
- candidate_required 时只允许 construction tools；
- no-crash 后强制 gate checkpoint；
- proximity 驱动 reinvestigation。

## Prompt 结构

### System prompt 只保留稳定规则

1. 目标：静态探索并构造 raw-input crash PoC；
2. 官方 `submit_poc` 是唯一动态 oracle；
3. 不使用本地 fuzz、GDB/LLDB、instrumentation 或运行目标；
4. 允许 BASH 做搜索、PoC 生成、hash/xxd/file；
5. no-crash 不说明失败 gate；
6. public crash 后停止；
7. 一次 candidate 尽量改变一个可解释控制量。

不要在多个 phase 文件重复这些规则。

### 每步 observation 固定为紧凑五块

```text
TASK: 高置信 anchors + unknown
HARNESS: entry/input/format/seed + evidence/gaps
CLAIM: 当前 supported claim 或缺口
CANDIDATE: hash/parent/delta/last public outcome
NEXT: 一个最优动作，不超过 3 条备选
```

总长度设明确上限；完整历史留在 artifact/memory，不反复注入。

### Candidate 路线只使用静态策略

允许建议：

- direct construct；
- boundary/length field edit；
- structure-preserving edit；
- qualified seed edit；
- 根据源码手工构造 state sequence。

禁止 prompt 建议：

- fuzz until crash；
- coverage-guided mutation；
- gdb/lldb/rr；
- 编译 sanitizer target；
- local verifier/container。

## Observation 纯函数要求

审查 `agent_impl/observations.py`：任何 render/helper 不得：

- 增加 observation_count；
- promotion/refute gate；
- prune suggestions；
- 改 mode/phase；
- 修改 candidate queue；
- 写 memory 文件。

所有 mutation 移到 reducer 的显式 event handler。测试对 state 做 deep-copy 前后比较。

## Prompt 文件清理

- 删除旧 `BOOTSTRAP/VERIFY/ACTION_REQUIRED` 术语残留；
- 合并重复 execution policy；
- `agent_prompts/phase/*` 最多保留两模式 + submit feedback 片段；
- 不删除兼容文件前先 `rg` 调用点；无调用的才删除；
- prompt snapshot 测试应断言语义，不精确锁死整段文案。

当前 `test_phase_engine_shape` 需要更新为两模式语义测试；不要为了让旧测试通过恢复四 phase。当前 layered tool schema failure 则必须先修正 legacy `current_phase` 与默认 `agent_mode` 的冲突，再更新断言，不能简单删除测试。

## 测试

1. render/prepare 两次 state 完全不变。
2. chain mode 可 WRITE/BASH 生成 candidate。
3. poc mode 可 targeted READ。
4. step 12 无 claim 时不会伪造 completeness 或硬锁工具。
5. no-crash 后仍可读/改 candidate，不自动 refute/切 mode。
6. ready candidate 可先做 byte sanity check 再提交。
7. duplicate candidate 被事实 gate 阻止。
8. system prompt 明确禁止本地 fuzz/debug/run。
9. prompt 不包含 fix-side 字段或 strict verifier hint。
10. crash 后 allowed actions 为空/Engine stop。

## 验收

- 一个持久 mode，其他控制标签均为派生/兼容；
- 工具门控不再造成“需要理解但不能 READ”的死锁；
- prompt 只展示可验证 artifact 和 unknown；
- render 纯函数；
- 没有本地动态分析建议；
- prompt token/字符数不高于改造前基线。

## 不要做

- 不再引入第三个 `strategy_mode`。
- 不因清理控制面重写整个 `agent.py`。
- 不删除 legacy 字段的反序列化支持；先停止写入，后续版本再删除。
