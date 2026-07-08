# v14-gdb-new assembled_messages 深审

这份补充审计专门针对最后一轮 `assembled_messages.jsonl`，重点看三件事：

1. 动态工具是否真的注入到可调用工具面；
2. Runtime Context 是否把 staged binary / runtime evidence / no-trigger 诊断放到了模型真正会服从的槽位；
3. context 是否存在噪声、错误优先级或互相冲突的信号，导致模型继续 submit/read 而不是诊断。

审计范围：

- timeout 轨迹：75 条，逐条读取最后一轮 assembled messages。
- 全量 TUI 工具列表：212 条 trace。
- 生成的逐条表：`assembled_context_audit.csv`。

## 最重要结论

这版不是“模型没有主动调用 dynamic tools”这么简单，而是 assembled 层同时存在三个断点：

1. **系统提示提到了 dynamic tools，但真实工具列表没有注入。**
2. **Runtime Context 没有把 dynamic diagnosis 作为 Next Action。**
3. **失败后的上下文缺少 active objective / mechanism graph / runtime evidence，噪声又很多，导致模型继续按旧流程 submit/read。**

硬统计：

| 检查项 | timeout 75 条结果 |
| --- | ---: |
| system prompt 提到 `run_candidate` / `probe_runtime_frontier` | 75 / 75 |
| TUI 工具列表包含 dynamic tool | 0 / 75 |
| 实际 dynamic action | 0 / 75 |
| Runtime Context 提到 dynamic tool | 0 / 75 |
| Runtime Context 提到 staged 或 `/out` | 1 / 75 |
| Runtime Context 有 Runtime evidence 槽位 | 0 / 75 |
| Active objective 非空 | 0 / 75 |
| 出现 `No mechanism graph yet` | 75 / 75 |
| submit 后没有 dynamic action | 71 / 75 |
| repeated miss 后 Next Action 仍非诊断 | 35 / 75 |
| repeated miss 后 `SUBMIT NOW` 覆盖 | 7 / 75 |

全量 212 条 TUI：

- 206 条有工具列表行，全部显示 `... (44)`。
- 0 条工具列表显式包含 `run_candidate` / `probe_runtime_frontier`。
- 0 次实际 dynamic action。

## 工具注入问题

### 现象

最后 assembled 的 system prompt 中确实包含类似规则：

```text
When dynamic tools are available and Next Action asks for runtime diagnosis,
run the exact latest candidate once with run_candidate before switching
objectives or resubmitting near-duplicate inputs.
```

但 TUI 开头工具列表对所有 timeout 都是：

```text
APPEND, BASH, CallsiteSearch, CorpusInspect, FileInfo, FindSymbols, GLOB, GREP, ... (44)
```

没有 dynamic tool，且工具数量仍是 44。全量 212 条也没有实际 `run_candidate` / `probe_runtime_frontier` action。

### 解释

这说明 dynamic tool 文案被注入到了 prompt，但 tool schema/action space 没有注入。模型看到的是“如果可用就用”，但实际上不能调用。

源码侧对应点：

- `agent_impl/tools/registry.py` 只有在 `dynamic_tools_enabled()` 为真时注册 `RunCandidateTool` 和 `ProbeRuntimeFrontierTool`。
- `dynamic_tools_enabled()` 依赖 `CYBERGYM_ENABLE_DYNAMIC_TOOLS`。
- `feedback/arbitration.py` 进一步要求 staged capability 和 invocation profile ready。

所以可能断点至少有三类：

1. 远端实际 import 的 bundled copy 没有同步到含 dynamic registry 的版本；
2. tool registry 构建时 env/capability/profile 条件不满足；
3. env 在 batch process 有，但 agent 构建工具面时没有传到同一进程/同一 import 路径。

仅从 assembled/TUI 可以确定的是：prompt 文案到了，tool schema 没到。

## Runtime Context 问题

### 1. Active objective 全部丢失

75 条 timeout 的最后 Runtime Context 都显示：

```text
Active objective: (none)
```

这很危险。v14 的 dynamic diagnosis schema 需要 `objective_id`，尤其 `probe_runtime_frontier` 要求 active objective。没有 objective，arbitration 很难把 no-trigger 归因到一个稳定目标，也很难形成 “同一 objective repeated miss” 的冷却。

实际后果：

- repeated submit 只能在 PoC 文件/feedback 层堆积；
- no-trigger 不能被映射成 objective_not_satisfied；
- Next Action 容易退回到泛泛的 READ / write / submit。

### 2. Mechanism graph 全部为空

75 条 timeout 最后都出现：

```text
No mechanism graph yet
```

但很多 context 同时又有 confirmed sink、source file、gate、candidate attempts。这说明当前 state 能记录零散事实，但没有把它们组合成可执行的 mechanism graph。

这会直接影响 gdb frontier：

- `probe_runtime_frontier` 需要 source-backed harness/parser/dispatch/pre_sink/sink 位置；
- 没有 graph，就算工具注册了，也可能只能 inconclusive；
- 模型看到的是一堆 read facts，而不是“从 harness 到 sink 的断点序列”。

### 3. Runtime evidence 槽位完全缺失

75 条 timeout 的最后 Runtime Context 没有任何 `Runtime evidence:` 行。

这当然部分是因为 dynamic tools 没有实际调用；但也说明 renderer 没有提供“缺失证据”的显式槽位。例如它没有说：

```text
Evidence gap: last candidate got no-trigger, but no local runtime evidence exists.
Best next action: run_candidate(...)
```

结果是 Experiments 只告诉模型“很多次 no crash”，却没有把它推到“先分类 miss”。

### 4. Staged binary 能力几乎不可见

75 条 timeout 最后 Runtime Context 里只有 1 条提到 staged 或 `/out`。这说明 staged binary capability 没有作为 runtime contract 的稳定字段进入 observation。

应该至少出现类似：

```text
Staged binary: available/unavailable/profile_unresolved/gdb_available
```

否则模型无法判断 dynamic tools 是否应该被使用；更糟的是，如果 system prompt 说“when available”，但 context 不显示 available，就会变成弱提示。

## Next Action 优先级问题

75 条 timeout 的最后 Next Action 分类：

| Next Action 类型 | 数量 |
| --- | ---: |
| submit / write-and-submit | 55 |
| read | 8 |
| other required gap | 12 |
| dynamic diagnosis | 0 |

这说明 v14 计划中的 “NO_TRIGGER -> runtime diagnosis” 没有进入最强执行槽位。

### 典型问题 1：20 次 no-crash 后仍推荐 READ

`others-b / arvo_12195`：

- steps=104
- submit=20
- Experiments 明确写了 “20 consecutive no-crash”
- Pattern 明确说 “Classify the miss before more variants”
- 但 Next Action 是：

```text
Recommended: READ the parser entry to confirm input format.
```

这里不是模型没看到失败，而是 context 自己没把失败转成 runtime diagnosis。20 次 submit 后还让 READ parser entry，已经太晚，也不是最便宜的判别动作。

### 典型问题 2：14 次 no-crash 后仍 `SUBMIT NOW`

`others-c / arvo_43599`：

- steps=94
- submit=17
- Experiments 中有 14 consecutive no-crash
- Next Action 仍是：

```text
SUBMIT NOW: submit_poc("pocs/poc_neg_subrs.otf")
Submit all 5 ready PoCs in this step.
```

这就是 ready PoC / submit pressure 覆盖 repeated miss。它等价于告诉模型：虽然已经失败很多次，但只要有 ready PoC，就继续提交。

### 典型问题 3：recipe gap 过泛，阻塞了提交/诊断

`v1-luke / arvo_23979`：

- steps=143
- submit=12
- Next Action 是：

```text
Required: Resolve recipe gap: Trigger: input_buffer == Data && input_size == Size — no input mapping
```

这个 gap 太泛，几乎是所有 fuzz harness 都成立的输入映射，不是一个可行动的漏洞条件。它会把模型拉回抽象 recipe 修补，而不是问：“上一批 PoC 到了哪个 parser/gate？”

### 典型问题 4：零提交 timeout 被 READ 拖住

`others-a / arvo_51010`：

- steps=31
- submit=0
- confirmed sink 已有；
- Next Action 是 READ sink/immediate caller，再写 PoC。

这类需要更强的 deadline policy：step 到 20+ 且 confirmed sink 已有时，不能继续 READ 泛化条件；应该要求最小 candidate 或明确记录无法生成的具体 blocker。

## Context 噪声和误导信号

### 1. Likely 区域噪声偏多

timeout 最后一轮平均每条：

- Confirmed：1.8 行；
- Likely：7.52 行；
- Supplementary：11.93 行；
- Unknown：6.03 行。

Likely/Supplementary 远多于 Confirmed，而且常出现分析服务 literal_text 命中。例如 `arvo_12195` 中大量 `DirectClass` / `PseudoClass` 命中落在 `Magick++/lib/Image.cpp`，但真实 active sink 是 `coders/tiff.c`。这类 literal hits 会稀释注意力。

### 2. Unknown 区域保留陈旧问题

75 条 timeout 都保留：

```text
Harness: which fuzzer targets the vulnerability?
Harness consumption: partial/unknown...
```

即便部分轨迹已经有 likely harness、submit feedback、PoC reaches harness 之类事实，Unknown 仍然不断提示 harness 未知。这会让模型在“已多次 submit 失败”后回到 harness/source reading，而不是 dynamic miss classification。

### 3. `PoC reaches harness (vul_exit=0)` 容易过度解读

多条 context 把 no-crash 的 exit 0 记录成：

```text
PoC reaches harness (vul_exit=0)
```

这个信号容易被模型理解成“已经到 harness，甚至 parser 也许正常”，但 `submit_poc` 的 no-crash 并不能说明到达目标 parser/sink，只能说明 vulnerable binary 正常退出。应改成更谨慎的表达：

```text
Target process exited normally; parser/sink reachability unknown.
```

否则它会削弱 dynamic frontier 的必要性。

### 4. Experiments 表格压缩过度

例如 20 次 submit 的轨迹，表格只展示 3 行，而且可能重复同一个 PoC 名。它丢了关键差异：

- 每次 candidate family 是否相同；
- 哪些字段改了；
- 是否换过 carrier；
- 是否命中过同一 failed gate；
- 哪次之后应该 cooldown。

模型只看到“很多 no crash”，但看不到“为什么这是同族重复”，也就很难自发停止。

## 对实现的具体判断

### P0-1：动态工具注册/远端 import 路径必须作为 smoke gate

当前不能再只检查 env。必须在每组启动后检查真实 tool list：

```text
tools line contains run_candidate and probe_runtime_frontier
```

如果没有，直接停止 batch；继续跑只会产生旧行为轨迹。

### P0-2：system prompt 不能无条件提不可用工具

如果 dynamic tools 没有注册，不应在 system prompt 里反复提示 `run_candidate` / `probe_runtime_frontier`。这会制造“计划存在但不可执行”的假象，也会污染审计。

更好的做法：

- tool registered + staged available：显示 dynamic tool policy；
- tool registered but staged/profile unresolved：显示 capability gap；
- tool not registered：不要显示 dynamic call instruction，只显示 “dynamic unavailable” 给日志/monitor。

### P0-3：Runtime Context 必须显示 dynamic capability 和 evidence gap

建议固定在 Current Assessment / Experiments / Next Action 中出现：

```text
Runtime capability: staged_binary=available profile=stdin gdb=true
Evidence gap: latest submit no-trigger, no run_candidate evidence for candidate digest=...
Next Action: run_candidate(...)
```

如果 profile unresolved：

```text
Runtime capability: staged_binary=available profile=unresolved
Next Action: resolve invocation profile, not submit more variants.
```

### P0-4：Next Action 优先级要硬改

当前 `SUBMIT NOW` 和 generic recipe/read blocker 仍会覆盖 repeated miss。应改成：

1. hard consistency / sanity block；
2. repeated no-trigger + dynamic available -> `run_candidate`；
3. runtime clean/input_rejected + gdb available -> `probe_runtime_frontier`；
4. repeated no-trigger + dynamic unavailable -> switch carrier/objective or repair harness/profile；
5. ready PoC submit；
6. generic READ / recipe gap。

也就是说，ready PoC 不能排在 repeated miss diagnosis 前面。

### P0-5：Active objective 不能在 submit 循环中一直为空

如果没有 active objective，至少要从 confirmed sink + latest candidate + failed gate 自动合成一个 transient objective。否则 repeated miss、runtime evidence、frontier probe 都没有锚点。

最低策略：

```text
if submit_count > 0 and active_objective is none:
    synthesize objective_id from active sink / ranked path / latest candidate family
```

### P0-6：机制图缺失时不要让 gdb frontier 失明

75/75 `No mechanism graph yet` 说明 mechanism graph builder 没吃到现有事实。短期可以 fallback：

- harness entry from detected fuzz target；
- parser/sink from confirmed sink candidate；
- gates from record_gate / Required Conditions；
- file:line from read facts。

即便 graph 不完整，也应生成 harness_entry / sink 两点 probe，而不是完全没有 frontier basis。

## 和之前报告相比的修正

之前报告说“动态工具被提示但没有被稳定调度”，这句话不够精确。深审后应修正为：

> dynamic tool policy 被注入进 system prompt，但 dynamic tools 没有进入真实工具列表；同时 Runtime Context 没有展示 staged capability、runtime evidence gap 或 dynamic Next Action。模型不是单纯没选择工具，而是在 assembled 层看到了不可调用的工具文案，以及仍然偏 submit/read 的执行指令。

这也解释了为什么 crash rate 低：v14 的核心闭环不是“弱了”，而是大部分情况下根本没有进入模型可执行的 action loop。

## 产物

- `assembled_context_audit.csv`：75 条 timeout 的最后 assembled 逐条结构化审计。
- `DEEP_ASSEMBLED_AUDIT.md`：本文件，聚焦工具注入、Runtime Context、Next Action、noise。
- `timeout_trace_reviews.md`：逐条 timeout 摘要，偏轨迹行为。
- `AUDIT_REPORT.md` / `OPTIMIZATION_PLAN.md`：总体审计和修复方向；其中关于 dynamic tools 的措辞应以后续深审为准。
