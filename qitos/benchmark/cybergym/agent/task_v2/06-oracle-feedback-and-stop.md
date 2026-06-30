# 06 — 统一官方 Oracle、反馈语义与停止条件

优先级：P0/P1  
依赖：[01](01-result-contract-and-serial-submit.md)、[05](05-candidate-experiment-contract.md)  
禁止：fix-side 泄漏、本地 verifier、从 no-crash 推断隐藏路径

## 最新再审计状态

状态：**未开始且当前回归加重；需要提前做最小止血 patch**。

最新 `feedback.py` 在原有 `NO_CRASH -> path_not_reached` 之上新增了 frontier/backward propagation：会根据历史 `crash_location` 或 raw output 猜 reached node，refute frontier，并降级更早 confirmed gate；找不到 frontier 时仍 refute 最早 gate。这些都不是 public no-crash 能证明的事实。

在完整任务依赖完成前，允许先提交一个最小止血 patch：

1. no-crash 不调用 `_refute_matching_gates()`；
2. 删除/禁用 path frontier 和 backward demotion；
3. no-crash 不更新 proximity/failed gate；
4. `PoCVerificationCriteria` 在 `vul_crashed()` 时停止；
5. 添加对应回归测试。

止血 patch 不引入新 enum/schema；完整 `OracleOutcome/FeedbackRecord` 仍按本任务后续步骤实施。

## 目标

把官方 `submit_poc` 作为唯一动态 oracle。只从公开可见字段产生保守事实；vul-side crash 后立即停止并保存首次 crash PoC。

## 必须复用

- `submit_tool.py` 现有 public/private sanitization
- `agent_impl/feedback.py::FailureType`、`FailureRecord`、`FeedbackRecord`
- `state.py::vul_crashed()`、`is_verified()`
- `stop_criteria.py::PoCVerificationCriteria`
- `agent.py::_process_action_result()` submit 分支

不要新增 judge/verifier agent。

## 文件

- 修改 `agent_impl/feedback.py`
- 修改 `state.py`
- 修改 `stop_criteria.py`
- 修改 `agent.py`
- 修改 `agent_impl/observations.py`
- 测试 `tests/test_agent.py`
- 新增 `tests/test_oracle_semantics.py`

## Public OracleOutcome

在现有 FailureType 附近定义单一 enum 或常量映射：

```text
VUL_CRASH
NO_CRASH
TIMEOUT
OOM
DUPLICATE
SUBMISSION_ERROR
UNKNOWN
```

映射优先级：

1. submit transport/status error；
2. duplicate 明确信息；
3. timeout/OOM 明确信息；
4. `vul_exit_code != 0` 或 server 明确 crash；
5. `vul_exit_code == 0`；
6. unknown。

ASAN type/location/stack 可以作为 crash 细节，但不能改变是否 crash 的主判断。

## 必须删除或降级的推断

### 删除

- `vul_exit_code == 0 -> path_not_reached`；
- no-crash 自动得到 proximity=1；
- no-crash 自动 refute 第一个 open gate；
- 无明确 stack 时猜 wrong location/signature；
- submission error 默认 carrier_parse。
- 基于历史 `state.crash_location` 对当前 no-crash candidate 做 frontier 判断；
- frontier refute 后对更早 gate 做 backward demotion。

### 保留但改为显式事实

- crash type/location：仅从公开 stderr/raw output 解析；
- timeout/OOM/duplicate：仅文本或结构字段明确出现时；
- `no_crash`：只写 candidate outcome，不改 chain/gate/format。

若旧 UI 需要 `failed_gate/proximity` 字段，写空值/unknown 兼容，不再用于 phase、family score 或 action gating。

## Stop Contract

运行时 public protocol 下：

```python
if state.vul_crashed():
    stop SUCCESS
```

- 不等待 fix-side；
- 不因“提高 precision”继续提交；agent 没有可用信息完成这件事；
- `is_verified()` 可保留给离线 evaluator/full protocol，但不能是 vul-only stop 的必要条件；
- stop message 明确是 `vulnerable target crashed`，不声称 strict differential accepted。

首次 crash 时：

1. 冻结 candidate hash/path；
2. 保存 `first_crash_candidate_id` 和 server-visible crash summary；
3. 禁止 reducer 后续状态覆盖；
4. Engine 下一次 stop check 必须结束。

## FeedbackRecord

每条 submit feedback 至少包含：

```text
candidate_id / artifact_sha256
action_id
oracle_outcome
poc_id
public exit code
public crash type/location/stack excerpt
raw evidence excerpt（有长度上限）
```

不保存或展示 `fix_exit_code`、fixed stderr、discriminant hint。即使 tool 内部接收到，也必须在进入 model/reducer strategy state 前剥离；离线 scorer 使用独立通道。

## 下一步策略规则

- `NO_CRASH`：claim 仍可疑；要求下一候选改变一个明确控制量或重新读取相关源码；
- `SUBMISSION_ERROR`：修复提交/path/size，不改漏洞假设；
- `DUPLICATE`：换 bytes，不增加 miss counter；
- `TIMEOUT/OOM`：本版本不本地诊断，只避免相同构造；
- `UNKNOWN`：保持 epistemic unknown；
- `VUL_CRASH`：停止。

## 测试

1. vul-only crash 触发 stop success。
2. no-crash 不改变任何 node/gate status。
3. no-crash 不产生 path_not_reached/proximity。
4. duplicate 不增加 semantic miss。
5. submission error 不变更 format/harness claim。
6. crash stack 可解析时仅添加细节。
7. fix-side 字段不出现在 observation、trace summary、FeedbackRecord。
8. 首次 crash candidate 被保存，后续 reducer 调用幂等。
9. full evaluator `is_verified()` 语义保持独立。
10. 修复现有 `test_vul_side_stop_criteria`。

## 验收

- public crash 立即停止；
- no-crash 是低信息 outcome，不是路径诊断；
- feedback 只含官方可见事实；
- stop、state、prompt、tests 对成功定义一致；
- 无本地动态分析或 fix-side 反馈回路。
