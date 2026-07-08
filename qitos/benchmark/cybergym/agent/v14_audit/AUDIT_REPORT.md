# v14-gdb-new 轨迹审计报告

审计对象：`/Users/morinop/Desktop/traj_analyzer/cybergym_workspace/remote_traces_v14_gdb_new_149_20260706_0856`

生成目录：`/Users/morinop/Desktop/traj_analyzer/cybergym_agent/v14_audit`

## 结论摘要

这批 `v14-gdb-new` 的 crash rate 低，不是因为模型完全没有生成候选，也不是因为 `submit_poc` 通道坏了；主因是 v14 规划里最关键的 “staged binary / gdb 动态诊断闭环” 没有真正进入执行行为。

补充深审修正：后续逐条读取 75 条 timeout 的最后一轮 `assembled_messages.jsonl` 后，可以把这个判断说得更精确：dynamic tool policy 被注入进 system prompt，但 dynamic tools 没有进入真实 TUI 工具列表；同时 Runtime Context 没有展示 staged capability、runtime evidence gap 或 dynamic Next Action。模型不是单纯没选择工具，而是在 assembled 层看到了不可调用的工具文案，以及仍然偏 submit/read 的执行指令。详见 `DEEP_ASSEMBLED_AUDIT.md` 和 `assembled_context_audit.csv`。

从 212 条本地拉取轨迹看：

| 指标 | 数值 |
| --- | ---: |
| trace 总数 | 212 |
| 已结束 | 180 |
| 触发 crash | 105 |
| budget_time 超时 | 75 |
| 仍在运行 | 32 |
| completed crash rate | 105 / 180 = 58.33% |
| all-trace crash rate | 105 / 212 = 49.53% |
| `submit_poc` 总调用 | 818 |
| 实际 `run_candidate` 调用 | 0 |
| 实际 `probe_runtime_frontier` 调用 | 0 |

最强信号是：系统 prompt / assembled messages 中已经出现 dynamic tool 和 staged binary 的说明，但 `tui.log` 里没有任何一次真实的 `run_candidate` 或 `probe_runtime_frontier` action。也就是说，v14 的能力在“被描述”，但没有被稳定调度、没有形成反馈闭环。

这会导致一个非常典型的失败形态：模型提交 PoC 后收到 no crash / no trigger，下一步不是用本地 staged binary 判断“没进 harness、parser 拒绝、dispatch 没选中、sink 未到、还是 trigger 条件没满足”，而是继续读源码、换参数、换大小、换相近格式，再 submit。于是 75 条 timeout 里有 71 条已经提交过候选，其中 45 条提交次数达到 5 次以上，12 条达到 10 次以上。

## 和 v14 规划的逐项对照

v14_next 的规划核心很清楚：

- 工作包 1：确认远端 staged vulnerable binary 可以挂载到容器 `/out`。
- 工作包 2：提供 `run_candidate` 和 `probe_runtime_frontier`，用于 NO_TRIGGER / 超时前拿结构化证据。
- 工作包 3：把动态证据写回 state / context / observation，让下一轮推理真的改变。

本批轨迹显示工作包 1 的环境方向大体已打通，但工作包 2/3 对模型行为的约束和闭环远远不够。

| v14 规划目标 | 轨迹里的实际表现 | 差距判断 |
| --- | --- | --- |
| staged binary 用于候选复现 | prompt 多次提到 staged binary，少数 assembled messages 提到 `/out` | 仅是上下文信息，未转化为稳定动作 |
| `run_candidate` 区分 clean / crash / timeout / rejected | 212 条 TUI 实际调用数为 0 | 工具注册、可见性、action suggestion 或 action gating 至少一处未闭环 |
| `probe_runtime_frontier` 定位 last-hit / first-unreached | 212 条 TUI 实际调用数为 0 | gdb frontier 没有成为 no-trigger 后的默认诊断路径 |
| NO_TRIGGER 后 feedback arbitration 建议动态诊断 | 818 次 submit 后仍主要继续手工变体和源码推断 | arbitration/action runner 没有形成软强制 |
| 动态证据进入 observation 改变下一步 | 轨迹中没有 runtime evidence 事实可供下一轮使用 | result processor / evidence ledger / renderer 闭环缺失或没有被触发 |
| 降低 `budget_time` | 已完成轨迹中 75 / 180 超时，超时率 41.67% | v14 最重要收益目标未达到 |

## 超时轨迹总体画像

75 条 timeout 的阶段和行为分布如下：

| 分类 | 数量 | 含义 |
| --- | ---: | --- |
| timeout with submit | 71 | 已有候选，但 no crash 后未收敛 |
| timeout without submit | 4 | 没有实际提交 PoC，候选生成压力不足 |
| timeout 最后停在 FORMULATION | 67 | 绝大多数死在“继续构造/修改”而非验证诊断 |
| timeout 最后停在 VERIFICATION | 7 | 有验证意图，但没有有效动态证据闭环 |
| timeout 最后停在 INVESTIGATION | 1 | 分析阶段过长 |

按 submit 次数分桶：

| timeout submit 次数 | 数量 | 审计含义 |
| --- | ---: | --- |
| 0 | 4 | early candidate policy 仍有漏洞 |
| 1 | 10 | 第一次失败后没有及时用动态诊断拆因 |
| 2-4 | 16 | 初步迭代无效，缺少分岔判断 |
| 5-9 | 33 | 典型 submit-spray / variant loop |
| 10+ | 12 | 高成本重复尝试，应该早就触发强制重规划 |

按 step 数分桶：

| timeout step 数 | 数量 | 审计含义 |
| --- | ---: | --- |
| 26-50 | 29 | 中早期没有拿到决定性证据 |
| 51-100 | 36 | 大量上下文和动作消耗在局部变体上 |
| 100+ | 10 | 已经明显进入循环，应由 watchdog / cooldown / forced diagnosis 打断 |

## 典型 timeout 病例

完整逐条摘要见 `timeout_trace_reviews.md`。这里列出最能代表问题的几类。

### 1. 零提交超时：candidate pressure 不够

代表轨迹：

- `v14-gdb-new-others-a / qitos_v14-gdb-new-others-a_glm-51_arvo_51010_20260705_155617_584319`
- `v14-gdb-new-others-b / qitos_v14-gdb-new-others-b_glm-51_arvo_65518_20260705_154920_711802`
- `v14-gdb-new-v2-vader / qitos_v14-gdb-new-v2-vader_glm-51_arvo_12662_20260705_155136_700330`
- `v14-gdb-new-v5-yoda / qitos_v14-gdb-new-v5-yoda_glm-51_arvo_11033_20260705_155646_784219`

共同点：

- 最后都在 FORMULATION。
- `submit_count=0`。
- 有的已经接近候选生成，甚至创建了 PoC 文件，但没有进入 `submit_poc`。
- 分析过程里存在 candidate_required guard、BASH 使用限制、源码读取/格式推断来回摇摆。

这说明 v14 “早候选、早 submit” 的设计偏好仍没有完全落实。特别是在候选已经写出但未提交的场景，agent 应该有一个非常直接的 rule：如果候选文件存在且预算进入后段，优先 submit 或给出阻塞原因，而不是继续扩展分析。

### 2. 高 submit 超时：no-trigger 后没有动态拆因

代表轨迹：

- `others-b / arvo_12195`：104 steps，20 次 submit，TIFF / GraphicsMagick 方向。
- `others-c / arvo_58770`：122 steps，18 次 submit。
- `others-c / arvo_43599`：94 steps，17 次 submit，PDF 方向。
- `v3-rey / arvo_19497`：98 steps，16 次 submit。
- `v3-rey / arvo_1976`：69 steps，15 次 submit。

共同点：

- 多次提交后仍围绕相近 candidate family 做局部变体。
- 日志里不断出现 “No crash observed” 一类反馈，但没有一次 `run_candidate` 或 `probe_runtime_frontier`。
- 模型用源码猜测解释失败原因，却没有 staged binary 证据判断是否已经到达 harness/parser/sink。

这类是当前 crash rate 的最大拖累。submit feedback 已经足够告诉 agent “上一条路没触发”，但系统没有把它转成 “必须拿一次 runtime evidence 再继续变体”。

### 3. 高 step 超时：上下文和动作循环成本失控

代表轨迹：

- `v1-luke / arvo_23979`：143 steps，12 次 submit，OpenSC / coolkey 方向。
- `v1-luke / arvo_29728`：125 steps，11 次 submit，MuPDF / xref 方向。
- `others-b / arvo_11523`：120 steps，10 次 submit，AV1 方向。
- `others-c / arvo_61816`：118 steps，9 次 submit。
- `v2-vader / arvo_21011`：113 steps，9 次 submit。

共同点：

- 分析和构造都很努力，但缺少一个低成本判别器。
- 失败后模型继续在格式细节里游走，靠自然语言推理判断“可能是哪个字段不对”。
- 没有 family cooldown：同一路线失败多次后，没有强制改变 carrier / selector / objective / parser path。

这类不一定是模型“笨”，更像系统没有给它刹车和仪表盘。没有 frontier 证据时，复杂格式任务很容易陷入“调参式构造”。

### 4. 模型知道 `/out`，但没有用封装工具

轨迹中少量日志出现了模型通过 BASH 手动尝试 `/out` 或 fuzz target 命令的迹象。这个细节很关键：说明模型并非完全不知道 staged binary，而是工具/动作层没有把 staged binary 使用收束到安全、结构化、短输出的 `run_candidate` / `probe_runtime_frontier`。

如果允许模型用 BASH 手动探 `/out`，会带来三个问题：

1. 输出不结构化，后续 reducer 和 observation 很难稳定吸收。
2. 没有统一 crash classification，容易把 usage error、clean exit、timeout、sanitizer report 混在一起。
3. 容易绕开安全和预算限制，和 v14 规划的 bounded execution 初衷相反。

## 为什么 crash rate 被拖低

本批 crash rate 低的原因可以归纳为五个层次。

### 1. “提交失败”没有变成“诊断问题”

`submit_poc` 是 benchmark oracle，但它的 no-trigger 反馈粒度很粗。v14 本来要用 staged binary 补这个粒度：先 local run 判断是否被 parser 拒绝，再用 gdb frontier 判断到没到 sink。

实际轨迹中，这个补粒度动作没有发生。于是 no-trigger 对模型来说只是一句“没撞”，下一步只能继续猜。

### 2. 工具存在感停留在 prompt，不在 action policy

assembled messages 里有 dynamic tool 文案，但 TUI 中实际调用为 0。这说明仅靠 prompt 告诉模型“你可以用某工具”不够。需要 action runner / feedback arbitration 在特定条件下明确建议甚至软强制：

- 第一次 no-trigger 后，建议 `run_candidate`。
- 同一 objective 连续两次 no-trigger 后，下一次 submit 前必须先拿 runtime evidence，除非模型给出明确的新 carrier/path。
- `run_candidate` clean/input_rejected 后，有 source-backed path 时建议 `probe_runtime_frontier`。

### 3. 重复候选缺少冷却机制

多条 timeout 出现 10 次以上 submit。很多 submit 只是在同一格式族、同一触发假设下调整大小、字段、压缩方式或对象布局。没有动态证据时，这种迭代很难判断是否有效；没有 cooldown 时，系统也不会打断。

### 4. 复杂格式任务需要“构造器 + validator + harness path”，当前格式能力仍偏浅

PDF、TIFF、font、AV1、archive、UPX、OpenSC、SAS7BDAT 等任务不是简单写几个 magic bytes 就能到达目标路径。模型经常能找到可疑代码，但候选被 parser 早早拒绝，或者没有选中目标 dispatch。

这说明 domain pack 不应只给概念知识，还要提供：

- 最小合法样本构造模板。
- harness 接受条件。
- parser gate 顺序。
- 可变异字段和不可破坏字段。
- 快速 validator / identify / dump 工具建议。

### 5. wall-clock / liveness 层面仍有风险

远端监控曾看到部分 trace 长时间 running、tmux/docker 状态与预期不完全一致。当前 `budget_time` 很可能主要在 step 间生效；如果 LLM call、tool call、容器执行或 batch worker 阻塞，单靠 agent 内部 stop criteria 不一定能及时回收。

这不会直接解释 75 条已 DONE timeout，但它会影响批测吞吐、容器残留和真实放量稳定性。v14 要大规模跑 600 / 1507，这一层必须硬化。

## 当前实现不到位的地方

按严重程度排序：

1. **动态工具未实际调用。** 这是 P0。212 条轨迹、818 次 submit、0 次 `run_candidate`、0 次 `probe_runtime_frontier`，说明能力没有进入主循环。
2. **NO_TRIGGER arbitration 不足。** no-trigger 后没有强制产生“缺少哪类证据”的诊断状态，也没有稳定建议下一步动态工具。
3. **runtime evidence ledger / result processor / observation 闭环缺位或未触发。** 即便工具存在，当前轨迹也看不到 runtime evidence 被 reducer 吸收并改变下一步。
4. **submit-spray 缺少 family cooldown。** 同一 hypothesis / candidate family 多次 no-trigger 后，仍继续局部变体。
5. **candidate pressure 对零提交 case 不够。** 仍有 4 条 timeout 没有 submit。
6. **格式能力偏“知识提示”，缺少可执行 scaffold。** 对复杂格式，模型需要可以复用的 builder/validator/harness recipes。
7. **工具命名和暴露方式可能降低可用性。** `run_candidate` / `probe_runtime_frontier` 从语义上还可以，但如果在工具列表中不显眼、描述不够硬、或被 BASH 等工具淹没，模型不会主动选。命名风格一致性不是当前最大 blocker，但“动态诊断工具在 action space 里的显著性”是 blocker。
8. **liveness/watchdog 不够硬。** 需要 batch/worker/container 级别的 wall-clock kill 和残留清理。

## 分组表现

| group | trace | done | crash | timeout | running | completed crash rate | timeout 平均 steps | timeout 平均 submit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v14-gdb-new-others-a | 25 | 21 | 11 | 10 | 4 | 52.38% | 46.50 | 3.60 |
| v14-gdb-new-others-b | 35 | 31 | 18 | 13 | 4 | 58.06% | 63.46 | 6.77 |
| v14-gdb-new-others-c | 34 | 30 | 17 | 13 | 4 | 56.67% | 77.85 | 9.00 |
| v14-gdb-new-v1-luke | 38 | 34 | 21 | 13 | 4 | 61.76% | 71.00 | 5.00 |
| v14-gdb-new-v2-vader | 33 | 29 | 18 | 11 | 4 | 62.07% | 73.73 | 5.09 |
| v14-gdb-new-v3-rey | 18 | 14 | 7 | 7 | 4 | 50.00% | 62.86 | 7.43 |
| v14-gdb-new-v4-leia | 18 | 14 | 10 | 4 | 4 | 71.43% | 69.00 | 5.50 |
| v14-gdb-new-v5-yoda | 11 | 7 | 3 | 4 | 4 | 42.86% | 38.75 | 2.00 |

注意：这份本地快照仍有 32 条 running，因此分组 crash rate 只适合看已完成轨迹，不应当当作最终批次成绩。

## 建议的验收指标

下一版不要只看最终 crash rate，至少加这些中间指标：

1. `dynamic_after_no_trigger_rate`：NO_TRIGGER 后 1-2 步内调用 `run_candidate` 的比例。
2. `frontier_after_clean_rate`：`run_candidate=clean/input_rejected` 后调用 `probe_runtime_frontier` 的比例。
3. `repeat_submit_without_new_evidence`：没有新证据时连续 submit 次数。
4. `timeout_with_>=5_submits`：高重复 timeout 数量。
5. `timeout_no_submit`：零提交 timeout 数量。
6. `runtime_evidence_to_next_action_change`：动态证据是否导致 carrier/selector/path/objective 改变。
7. `container_orphan_count`：tmux/batch 退出后残留 qitos 容器数。

## 附件文件说明

- `summary_numbers.json`：全局核心数字。
- `group_stats.csv` / `group_stats.json`：按 8 个 group 的统计。
- `trace_index.csv` / `trace_index.json`：212 条 trace 的逐条索引。
- `timeout_trace_reviews.md`：75 条 timeout 的逐条摘要、证据片段和初步诊断。
- `OPTIMIZATION_PLAN.md`：按 P0/P1/P2 拆分的修复方向。
