# 04 — 把 Chain/Gate/Trigger 改成证据约束模型

优先级：P1  
依赖：[03](03-static-scope-and-harness-contract.md)  
禁止：新 call-graph 引擎、动态 coverage、instrumentation、本地执行

## 最新再审计状态

状态：**存在反方向的部分实现，必须先替换再扩展**。

可复用的新骨架：`agent_mode`、两模式 phase、Chain Work Order、`chain_completeness_score()` 的调用点。

必须删除/替换的当前行为：

1. `chain_completeness_score()` 直接信任模型可写的 `confirmed`；
2. `reorder_chain_nodes()` 实际未使用 call graph，却会重编号 node；
3. reducer 在 `record_chain_node` 后无条件 reorder；
4. `_constraint_board_lines()` 按展示次数 auto-promote suggestion；
5. suggestion 找不到 node 时仍可能使用 node order 0；
6. Chain Work Order 指示模型直接写 `status='confirmed'`；
7. 新 prompt 用 score 0.3/0.4 当硬门槛。

本任务第一提交应只做 schema/evidence/purity migration，不要先调 score 权重或增加更多 gate pattern。

## 目标

保留当前 `ChainNode`、`ChainGate` 和 tracking tools，但禁止模型无证据自称 `confirmed`。把 chain 从 prompt ledger 改为“可追溯的局部 input-to-target 路径”。

## 必须复用

- `state.py::ChainNode`、`ChainGate`
- `tracking_tools.py::RecordChainNodeTool`、`RecordGateTool`
- `repo_index.py::trace_call_chain_structured()`
- `repo_index.py::find_dispatch_tables()`、`find_indirect_dispatch()`
- GREP 的 `match_id`、READ 的 path/line output
- `agent_impl/state_init.py::_precompute_call_chain()`

不要新增 `RouteGraph` 平行体系；直接强化现有记录并提供兼容迁移。

## 文件

- 修改 `state.py`
- 修改 `tracking_tools.py`
- 修改 `agent_impl/state_init.py`
- 修改 `agent_impl/observations.py`
- 修改 `agent_impl/phase.py`
- 测试新增 `tests/test_evidence_chain.py`

## Schema 改动

给 `ChainNode` 和 `ChainGate` 增加：

```python
evidence_refs: list[str] = field(default_factory=list)
confidence: float = 0.0
producer: str = "model"       # model | repo_index | reducer
```

若现有 `evidence` 是字符串，保留兼容读取，但新写入统一转换为 ref。推荐 ref 格式：

```text
read:<workspace-relative-path>:<start>-<end>
grep:<match_id>
repo:<index-fingerprint>:<symbol>:<edge-kind>
harness:<contract-field>
submit:<poc-id>:<visible-signal>
```

ref 只能指向已有 artifact/tool result；不得写自然语言当 ref。

## 状态语义

状态收敛为：

- `hypothesized`：模型提出，允许无 evidence；
- `supported`：至少一个静态 evidence ref 通过 validator；
- `refuted`：存在明确相反源码证据；
- `observed`：仅用于官方 submit 输出明确出现的 crash location/stack；
- legacy `confirmed` 读取时迁移为 `supported`，除非已有 runtime observation ref。

模型工具参数不得再接受 `confirmed/observed`。promotion 只能由 reducer validator 完成。

## 实现步骤

### 1. EvidenceRef validator

在现有模块内实现小函数，不建 evidence database：

- `read:` 检查路径在 workspace 且 line range 合法；
- `grep:` 检查 match id 存在于当前 search cache；
- `repo:` 检查 fingerprint 与当前 index 一致；
- `harness:` 检查 contract 字段有来源；
- `submit:` 只允许 visible output 字段。

validator 返回 bool + reason；失败时 tracking tool 返回可修复错误，不写 state。

### 2. RecordChainNodeTool

- `hypothesized` 可无 ref；
- 请求 `supported` 必须传 ref；
- 记录 source path/line 时优先生成 `read:` ref；
- 同一 function/path 合并 evidence，不重复 node；
- 不因 description symbol 自动 promotion。

### 3. RecordGateTool

- gate 必须挂到真实 node id/order；找不到 node 时拒绝，不再默认为 node 0；
- gate 文本拆出 `predicate` 与 `input_control`，例如 `len_field > buffer_size`；
- `supported` 必须引用含 predicate 的源码 span；
- `no_crash` 不能调用 refute；
- refuted 必须引用相反 condition、unreachable edge 或明确 source evidence。

### 4. TriggerClaim

复用 `trigger_hypothesis` 字段作为兼容 summary，并在 metadata/嵌套 dataclass 保存：

```python
TriggerClaim(
    claim_id,
    target_node,
    predicate,
    input_controls,
    evidence_refs,
    status,          # hypothesized | supported | refuted
)
```

一个 claim 必须回答：改哪些输入控制量、预期触发哪个 bad state。bug label 不是 claim。

### 5. 修正 chain ordering/completeness

当前 `reorder_chain_nodes()` 只按 role 排序，却声称使用 call graph。二选一：

- 根据 evidence-backed edge 显式排序；或
- 删除自动排序，保留 model/repo index 给出的稳定 order。

`chain_completeness_score()` 不再直接计算“有多少 status=confirmed”。改成只统计 validated artifacts：

- harness entry contract；
- 至少一个 supported target node；
- 至少一个 supported trigger claim；
- entry→target 的静态 edge evidence（缺失时明确为 gap）。

分数只能用于 observation，不得作为硬 action gate。

在当前实现上优先选择“停止自动 reorder”：保留记录时的稳定 node id/order，直到存在显式 evidence-backed edge。禁止按 role 重编号后让已有 `ChainGate.node_order` 静默指向别的 node。

### 6. 初始化预计算降级

`_precompute_call_chain()`：

- 只有 repo index 找到真实 definition/edge 时才创建 supported node；
- 普通 description anchor 只创建 search hint；
- 不自动创建 format gate；
- callback/table edge 用现有 dispatch helpers 标注 edge kind。

## 测试

1. `record_gate(status="supported")` 无 ref 被拒绝。
2. 缺失 node 的 gate 被拒绝，不挂 node 0。
3. 合法 READ span 可 promotion。
4. description-only symbol 仍是 hypothesized。
5. no-crash reducer 后 supported gate 不变。
6. 相反源码 span 可 refute。
7. stale repo fingerprint ref 被拒绝。
8. legacy confirmed state 可加载并迁移。
9. render 两次不改变 observation_count/status/evidence。
10. chain completeness 不能靠写三个假 node 提升。

## 验收

- 所有 supported/observed/refuted 均有合法 evidence ref；
- 模型不能直接创建 confirmed truth；
- gate 不会错误挂载；
- observation render 纯函数；
- 不新增全仓 call graph 或动态分析；
- prompt 展示“证据缺口”，不展示虚假完整度。
