# v14-gdb-new 优化方向

目标：把 v14 已经规划好的 staged binary / gdb 能力从“prompt 中可见”推进到“NO_TRIGGER 后稳定被调用、证据进入 state、下一轮动作改变”，优先降低 `budget_time`，其次提升 completed crash rate。

## P0：必须先修，否则 gdb/binary 版本没有意义

### 1. NO_TRIGGER 后自动插入动态诊断建议

触发条件：

- `submit_poc` 返回 no crash / no trigger。
- 当前 candidate 没有对应 runtime evidence。
- 同一 objective 连续 2 次 no-trigger。
- 当前 state 是 `revisiting_after_miss`，且存在最近 candidate 文件。

期望行为：

```text
submit_poc no-trigger
  -> feedback arbitration: missing local execution evidence
  -> next-action suggestion: run_candidate(candidate_path, purpose=classify_no_trigger)
```

这不需要锁死工具面，但应该是强提示。对于连续 2 次 no-trigger 且 candidate family 未变的情况，建议升级为软强制：下一次 submit 前必须先有 runtime evidence，除非模型显式切换 carrier / parser path / objective。

验收：

- 10 个历史 timeout canary 中，NO_TRIGGER 后 1-2 步内 `run_candidate` 调用率 ≥ 80%。
- `repeat_submit_without_new_evidence` 显著下降。

### 2. `run_candidate` 结果必须进入 runtime evidence ledger

需要确保 result processor 做这些事：

1. 校验 result schema。
2. 计算 candidate digest。
3. 写入 `.agent/runtime_evidence/` 原始输出。
4. 写短结构化记录到 state metadata。
5. bump context revision。
6. observation 的 Candidate State / Experiments / Next Action 能看到结论。

最低可用结论：

- `input_rejected`：优先修 magic/header/length/checksum/container。
- `clean_exit`：说明 harness 跑完但没触发，优先 probe path 或换 trigger condition。
- `timeout`：判断是否可作为 DoS，或缩小到 crash oracle 接受的失败形态。
- `sanitizer_failure` / `signal_failure`：提醒仍需 `submit_poc`，不能直接当 benchmark success。
- `environment_error` / `profile_unresolved`：不要污染 candidate 诊断。

验收：

- TUI 中能看到实际 `run_candidate` action。
- assembled observation 中能看到短 runtime evidence，而不是大段 stdout/stderr。
- 同一 candidate 的旧证据不会污染新 candidate。

### 3. `run_candidate` clean/input_rejected 后接 `probe_runtime_frontier`

触发条件：

- `run_candidate` 结果为 clean exit 或 input rejected。
- state 中存在 source-backed path / sink / parser gate。
- 当前 candidate 已经连续 no-trigger 或高风险 timeout。

期望行为：

```text
run_candidate clean/input_rejected
  -> probe_runtime_frontier(candidate_path, objective_id, probe_roles=[harness_entry, parser_accept, dispatch, pre_sink, sink, trigger_condition])
  -> observation: last-hit / first-unreached
  -> next action: 修 selector / carrier / parser gate / trigger condition
```

验收：

- gdb 输出不直接灌 prompt，只显示 `last_hit`、`first_unreached`、`exit_kind`、`evidence_ref`。
- 模型下一步必须改变一个可命名维度，例如 carrier、selector、object layout、length gate、checksum，而不是继续调同一个 size。

### 4. 加 no-evidence submit cooldown

当前 timeout 中 45 条提交次数 ≥ 5，12 条提交次数 ≥ 10。必须限制无新证据的重复 submit。

建议规则：

- 同一 candidate family 连续 2 次 no-trigger：必须产生新 runtime evidence 或切换 family。
- 同一 objective 连续 4 次 no-trigger：强制重规划，输出 “why previous family failed”。
- 同一 trace submit ≥ 8 且无 crash：提高 dynamic diagnosis 优先级，降低源码继续阅读优先级。

candidate family 可以粗略用这些字段计算：

- task id
- objective id
- generator script / poc basename
- carrier format
- target parser / sink
- core mutation axes

验收：

- `timeout_with_>=10_submits` 大幅下降。
- high-submit timeout 不再出现 15-20 次同族 submit。

### 5. 修 batch / worker / container liveness

虽然这不是 crash rate 的唯一原因，但远端大规模跑必须硬化。

建议：

- 每个 trace 有外层 wall-clock watchdog，不依赖 agent step 间 stop criteria。
- LLM call、tool call、dynamic execution 都有单次 timeout。
- batch worker 超时后 kill 当前 trace 的进程树。
- qitos container 加 label：run prefix、group、trace id、start time。
- tmux session 退出后执行 container cleanup；cleanup 失败要写 run-level warning。
- 监控脚本报告 orphan containers，而不是只看 `docker ps` 数量。

验收：

- tmux 退出后对应 qitos 容器 1-2 分钟内消失。
- running trace 不会长期超过配置预算。
- hourly report 可以区分正常 running、stale running、orphan container。

## P1：直接影响成功率，但可以在 P0 闭环之后做

### 6. 零提交超时保护

本批有 4 条 `timeout_no_submit`。虽然数量不多，但这类是纯浪费。

建议规则：

- step ≥ 20 且没有 submit：observation Next Action 只能是 `WRITE/BASH create candidate` 或 `submit_poc`，除非确实缺少 task/harness 信息。
- 检测到 `pocs/` 下新文件但未 submit：下一步优先 submit。
- candidate_required guard 不应让 agent 卡住；如果 BASH 被拒绝，observation 应给替代动作：`GREP` / `READ` / 最小候选。

验收：

- `timeout_no_submit` 接近 0。

### 7. 把格式知识从“提示”变成“可执行 recipe”

优先覆盖这批 timeout 高频域：

- TIFF / GraphicsMagick / libtiff
- PDF / MuPDF / xref / object stream
- font / CFF / TrueType / kerx
- AV1 / IVF / keyframe / tile / OBU
- archive / UPX / Mach-O / pack header
- XML / HTML / parser entity / encoding
- OpenSC / smartcard object layout
- SAS7BDAT / binary table metadata

每个 domain pack 最少给：

1. 最小合法样本 builder。
2. parser 接受条件。
3. 关键 checksum / length / offset 自动回填。
4. harness invocation / accepted extension / stdin-file mode。
5. 常见 first-unreached gate 和修复建议。

验收：

- format-heavy timeout 的 `input_rejected` 比例下降。
- 同类任务的第一次有效 submit step 提前。

### 8. harness / invocation profile 质量提升

`run_candidate` 依赖正确 invocation profile。如果 profile unknown，动态工具会变成摆设。

建议：

- 从 QitOS staged metadata、task description、server invocation、fuzz target 名称中合并 profile。
- profile unresolved 时，在 observation 中明确“先解析 invocation profile”，而不是继续要求 dynamic run。
- 对 stdin/file argv 两类目标分别测试。

验收：

- staged binary available 的 trace 中，`profile_unresolved` 率低于 10%。

### 9. observation 中增加“证据缺口”而不是只列状态

当前模型经常知道自己失败了，但不知道缺哪类证据。observation 应明确：

```text
Evidence gap: candidate was submitted and got no-trigger, but there is no local runtime evidence.
Best next action: run_candidate on the last submitted PoC before another mutation.
```

或者：

```text
Frontier gap: parser_accept hit, sink not reached.
Best next action: repair dispatch selector, not overflow size.
```

验收：

- assembled messages 中能看到短、单一、可执行的 next action。
- 下一步 action 与 evidence gap 匹配。

## P2：提高上限和可维护性

### 10. 工具命名和显著性整理

工具名风格一致性确实有问题，但不建议在当前紧急版本里大规模重命名。真正要修的是“关键工具在 action space 里不显眼”。

短期建议：

- 保留 `run_candidate` / `probe_runtime_frontier` 名称，避免破坏已有代码。
- 在工具 description 第一行写清楚触发场景：`Use after submit_poc returns no crash/no trigger before more mutations.`
- observation Next Action 直接点名工具。
- 如果工具列表太长，把 dynamic tools 排到更靠前或在 prompt 中独立列一小节。

中期再统一：

- 选择动词风格：`run_*`、`probe_*`、`submit_*`、`record_*`。
- 避免大小写混杂和近义重复。
- 给高价值工具加 alias 需谨慎，因为 alias 会增加工具面。

### 11. casebook 回归集

从这批 75 条 timeout 里抽一个固定回归集：

- 4 条零提交 timeout。
- 10 条最高 submit timeout。
- 10 条最高 step timeout。
- 10 条不同格式域 timeout。

每次改 agent 后跑 canary，比较：

- crash rate。
- timeout rate。
- first submit step。
- submit count。
- dynamic tool invocation rate。
- runtime evidence 是否改变下一步。

### 12. 结果归因面板

建议在批测统计里增加归因，不然只看 crash rate 很难定位版本变化：

| 指标 | 解释 |
| --- | --- |
| `first_submit_step_avg` | 是否仍保持早提交优势 |
| `no_trigger_to_dynamic_steps_p50` | no-trigger 后多久拿动态证据 |
| `dynamic_tool_success_rate` | 工具是否环境可用 |
| `frontier_inconclusive_rate` | gdb probe 是否缺 source-backed path |
| `candidate_family_repeat_rate` | 是否仍在局部 submit-spray |
| `timeout_after_dynamic_evidence` | 动态证据是否真的降低 timeout |

## 推荐实施顺序

1. 先做 P0-1 / P0-2：NO_TRIGGER -> `run_candidate` -> evidence observation。
2. 再做 P0-3：clean/input_rejected -> `probe_runtime_frontier`。
3. 同时加 P0-4 cooldown，防止继续 10+ submit。
4. 加 P0-5 liveness，确保 149 放量稳定。
5. 用 24 条 timeout canary 先跑，不要直接全量。
6. canary 指标达标后，再跑 100；100 不回退再上 600/1507。

## 对下一版的最低通过线

如果下一版仍然出现：

- `run_candidate` 实际调用数为 0；
- `probe_runtime_frontier` 实际调用数为 0；
- 10+ submit timeout 大量存在；
- timeout_no_submit 仍出现；

那就说明 v14 的 gdb/binary 修复仍没有进入主循环，不应继续扩大批测。反之，即使 crash rate 一开始只小幅提升，只要 dynamic-after-no-trigger 和 repeat-submit 指标明显改善，就说明方向是对的，可以继续补 domain pack 和 frontier path。
