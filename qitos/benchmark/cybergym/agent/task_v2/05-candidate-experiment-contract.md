# 05 — 将 Candidate 变成可归因的字节实验

优先级：P1  
依赖：[01](01-result-contract-and-serial-submit.md)、[03](03-static-scope-and-harness-contract.md)、[04](04-evidence-backed-chain.md)  
禁止：fuzz loop、随机批量喷射、本地运行目标

## 最新再审计状态

状态：**未开始**。

最新两模式只改变何时允许构造 PoC，没有解决 candidate bytes identity、parent、claim、delta 或 result attribution。当前 prompt 仍鼓励“submit immediately”与 corpus-first，但 candidate contract 仍不足。

本任务必须在任务 01 完成后实施；否则即使新增 hash/claim，parallel result 仍可能归错 candidate。不要把 mode 切换或 completeness 分数写入 CandidateRecord。

## 目标

复用现有 `CandidateRecord`、candidate queue 和 artifact store，使每个提交都能回答：文件 bytes 是什么、从哪个 parent 变来、验证哪个 claim、精确改了什么、官方结果属于谁。

## 必须复用

- `family_runtime.py::CandidateRecord`
- `agent_impl/candidates.py`
- `artifact_store.py`
- `state.ready_pocs`、`candidate_queue`、`submitted_fingerprints`
- `agent_impl/feedback.py::_submitted_candidate_context()`
- `submit_tool.py::_file_content_fingerprint()`

不要创建第二个 CandidatePool，不新增 worker runtime。

## 文件

- 修改 `family_runtime.py`
- 修改 `agent_impl/candidates.py`
- 修改 `submit_tool.py`
- 修改 `agent_impl/feedback.py`
- 修改 `tracking_tools.py::RecordAttemptTool`
- 测试新增 `tests/test_candidate_experiment.py`

## CandidateRecord 最小扩展

现有字段中保留：`candidate_id`、`family_id`、`file_path`、`base_seed`、`generation_method`、`artifact_sha256`、`hypothesis_ref`。

新增或规范：

```python
parent_sha256: str = ""
claim_id: str = ""
exact_delta: str = ""
expected_observation: str = "crash"
artifact_size: int = 0
seed_provenance: str = ""
```

`content_fingerprint` 统一等于文件 bytes SHA-256；旧 logical fingerprint 单独保留在 `logical_fingerprint`，不得用于提交去重。

## 实现步骤

### 1. 单一 bytes identity

提供一个共享 helper：

```python
def fingerprint_candidate(path, workspace_root) -> tuple[str, int]:
    # resolve under workspace/poc dir
    # stream SHA-256
    # return hash, size
```

`submit_tool.py`、direct candidate、delegate candidate 都调用它。禁止 path hash、mutation text hash 冒充 content hash。

### 2. candidate 注册时冻结 provenance

注册时立即读取并记录 hash/size。提交前再次计算：

- 相同：继续；
- 不同：更新为新 candidate id，旧 record 标记 superseded；
- 绝不能把变化后的文件结果记给旧 hash。

candidate id 推荐 `cand:<sha256前12位>`；相同 bytes 不因文件名不同而重复提交。

### 3. parent 与 exact delta

- 从 seed/上一 candidate 修改：必须记录 parent hash；
- parent artifact 可访问时计算 byte diff summary：changed ranges、old/new length、最多前 8 个 range；
- 从零构造：parent 为空，delta 描述 encoding/structure；
- 不保存大块 bytes 到 state，artifact store 保存文件。

### 4. claim 绑定

- supported claim 存在时必须填 `claim_id`；
- 探索性候选允许 `claim_id="exploratory"`，但每个 family 最多一个连续 exploratory submit；
- `mutation_summary` 必须描述输入控制量，不接受“try another variant”；
- `expected_observation` 在本版本只能是 `crash` 或明确 server-visible signal，不能写 `path reached` 等不可观测事实。

### 5. 静态 seed qualification

复用任务 03 的 `HarnessContract.seed_paths`。候选若以 seed 为 parent：

- 记录 workspace-relative provenance；
- 记录 format/magic/path/source-reference qualification reason；
- 不运行 seed；
- 未 qualified 的文件仍可人工选用，但必须标 `unqualified`，不能触发 corpus-first 自动路由。

### 6. 提交队列纪律

- submit 串行；
- 默认一次只暴露优先级最高的 ready candidate；
- 可一次生成多个确定性候选，但每个都有独立 hash/claim/delta；
- 已提交 hash 永不再次提交；
- crash candidate 立即复制/索引为 best/first crash artifact，不被后续文件覆盖。

### 7. RecordAttempt 直接引用 candidate

`RecordAttemptTool` 不再重复手填 path/result。只接受 candidate id + strategy note；reducer 从 CandidateRecord/FeedbackRecord 补齐其它字段，防止 ledger 与事实不一致。

## 允许的 generation_method

```text
direct_construct
boundary_value_edit
length_field_edit
structure_preserving_edit
qualified_seed_edit
state_sequence_construct
```

禁止新增 `fuzz`、`random_mutate`、`coverage_guided`。若用 Python/BASH 生成多组边界值，必须是有限、可解释、逐个有 claim 的确定性枚举。

## 测试

1. 同 bytes 不同 path 得到同 hash并去重。
2. 同 path 内容变化生成新 candidate，不污染旧 record。
3. parent diff ranges 正确且有大小上限。
4. 已提交 hash 被拒绝。
5. 两 candidate result 乱序输入仍按 action/candidate id 关联。
6. exploratory 连续提交受到软限制。
7. unqualified seed 不自动选择。
8. crash candidate 保存后原文件变化也不影响归档。
9. legacy CandidateRecord 可加载。

## 验收

- 每个 submit 都有 immutable bytes hash；
- candidate→action→feedback 可唯一追踪；
- candidate mutation 可由 parent/delta 复现；
- 不存在 path/logical fingerprint 混用；
- 无随机 fuzz/batch spray；
- 不运行本地目标。
