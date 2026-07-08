# vNext Context 与 Prompt 设计契约

本文是 5 个 vNext 实施任务的共同约束，具体实现必须同时满足 [`context_design.md`](../context_design.md) 的 V13 原则。它不是第 6 个子任务，而是 Task 01–05 的横切验收标准。

## 1. 核心原则

新能力只有完成以下闭环才算交付：

```text
分析器产出结构化 IR
  -> typed state / ArtifactStore
  -> 六段式 section renderer 消费
  -> phase prompt 指导模型如何使用
  -> Next Action 选出唯一当前 blocker
  -> delta/compaction 后仍能恢复
  -> end-to-end observation 测试验证模型实际看到的内容
```

禁止只完成“后台计算 + state 字段”，也禁止把 analysis 返回的 Markdown/JSON 直接附加到 observation。

## 2. 固定六段式信息架构

只能使用以下一级标题，顺序固定：

1. `Mission`
2. `Current Assessment`
3. `Vulnerability Path`
4. `Required Conditions`
5. `Experiments`
6. `Next Action`

### 当前实现需要同步修正的偏差

`agent_impl/observations.py::_render_observation()` 当前还生成 `Foundation` 和 `Allowed Tools` 一级标题；`_render_required_conditions()` 还会嵌入 `## Constraint Analysis Diagnostics`。Task 05 必须完成：

- 删除 `Foundation`，其不可缺少的信息合并回 `Mission`；delta 模式用内部 always-visible 摘要机制，而不是新增用户可见 section。
- 将 phase-allowed tools 作为 `Next Action` 的紧凑子项，或由 system/phase prompt 约束，不能作为第 7 个一级 section。
- 将 diagnostics 变成 `Required Conditions` 下的普通条目或 `### Diagnostics`，不得使用新的 `##`。
- end-to-end 测试断言 observation 中恰好出现这 6 种一级标题的允许子集，且顺序固定。

## 3. 新能力到六段式 Context 的唯一映射

| 新信息 | 唯一展示位置 | 不应出现的位置 |
|---|---|---|
| 原始漏洞描述、crash type、总体输入类型 | Mission | Assessment、Path 重复描述 |
| verified description refs | Current Assessment > Likely/Confirmed | 独立 Description Analysis section |
| unresolved/stale description hints | Current Assessment > Unknown/Rejected | 永久 WARNING |
| harness selection 与 consumption pattern | Mission（紧凑输入摘要）+ Current Assessment（证据状态） | 独立 Harness Analysis section |
| Top-K candidate paths | Vulnerability Path | Current Assessment 再列一份完整路径 |
| active/selected path | Vulnerability Path，标记 ACTIVE | Mission |
| sink argument provenance、byte mapping | Required Conditions | 原始 trace JSON、独立 Byte Layout section |
| mapping gap | Required Conditions 的 `?` 条目；若它是第一 blocker，再投影到 Next Action | Current Assessment 重复列出 |
| submit outcome 对 prior/path/gate 的修正 | Experiments；修正后的事实回写对应 section | orphan feedback block |
| 推荐工具与 stop condition | Next Action | 独立 Allowed Tools section |

## 4. Provenance 与信任顺序

所有事实使用统一标签，不允许同一来源出现多个叫法：

- `[source: submit_poc feedback]`：oracle，最高优先级。
- `[source: code reading]`：模型 READ 后确认。
- `[source: analysis service]`：source-backed static result；必须附 file:line 或 path ID。
- `[source: model_candidate]`：模型提出、未被代码验证。
- `[source: description]`：自然语言 prior。
- `[source: bootstrap fallback]`：regex/name/corpus 推断。
- `[source: unresolved]`：未知，不是反证。

覆盖规则：submit feedback > code reading > analysis service > model candidate > description > fallback。更低来源不能覆盖更高来源，只能产生冲突项或 next action。

每个新 dataclass 应保存 machine-readable provenance；renderer 统一格式化标签，禁止业务代码预先拼 Markdown source 字符串。

## 5. Context 生命周期与预算

### Description refs

- ingestion：最多 6 个 verified refs + 4 个 unresolved hints。
- exploration：只显示会影响 Top-K path 的最多 4 个 refs。
- investigation 以后：若 ref 已体现在 active path 中，从 Assessment 消失；stale ref 进入 Rejected 最多 3 steps。

### Harness consumption

- 未确认时显示 pattern + 最大 3 条 evidence。
- 确认后 Mission 只保留一行 `Input: pattern/magic/selector`；详细 evidence 从 Assessment 衰减。
- selection 改变时强制 full refresh。

### Ranked paths

- exploration：Top-3，便于选择；只有分数接近或 active path 失效时显示到 Top-5。
- investigation：active path 全链 + 最多 2 条 alternatives。
- formulation/verification：只显示 active path 和被 submit feedback 影响的 alternative。
- chain 最多 6 个可见节点；中间节点折叠，但 entry、dispatch、sink 不得折叠。

### Input mappings

- Required Conditions 总条目仍受 12 条总上限约束。
- confirmed mapping 优先于 inferred/open condition；同一 mapping 只出现一次。
- 最多 4 条可用 mapping + 2 条关键 unresolved mapping。
- raw trace steps 永不进入 observation；完整 trace 留 ArtifactStore。

## 6. Delta 与语义事件

section hash 可以保证内容变化被渲染，但以下事件应强制 full brief，因为它们会改变整个调查框架：

- description analysis 从 pending → recorded/verified。
- selected harness 或 consumption pattern 改变。
- active ranked path 改变或原路径被否定。
- sink analysis 首次产生 confirmed input mapping。
- submit feedback 使 active path/gate/mapping refuted。

实现方式：在 `state.metadata["_vnext_context_revisions"]` 保存 `description/harness/path/mapping/feedback` 的整数 revision；`_render_observation()` 把 revision snapshot 加入 semantic events。revision 是内部 bookkeeping，绝不能渲染给模型。

必须继续遵守 deep-copy previous hashes/revisions 后再写新值的规则。

## 7. Compaction 与证据保真

新能力的摘要必须来自 typed state，而不是依赖旧 tool output 留在 history：

- verified refs、harness pattern、active path、Required Conditions 和 mappings 必须可从 state/ArtifactStore 重建。
- `PostCompactRestorer` 在 compaction 后清除 V13 section hashes，强制下一 observation 全量再生。
- restored system message 只保留一个紧凑 Investigation Brief：active sink/path、第一 open gate、关键 byte mappings、最近 submit outcome。不要分别追加多个重复消息。
- span summary prompt 的 durable sections 增加 `Active Vulnerability Path` 和 `Concrete Input Constraints`，但它们只是 history 摘要；authoritative truth 仍是 state。
- READ/GREP/trace 原始输出可被 snip；一旦其事实已进入 typed state/gate/mapping，就不得依赖 raw output。

## 8. Prompt 分层

### System prompt

`agent_prompts/system/runtime_context_protocol.md` 只描述稳定协议：

- 六段式 section 的语义。
- provenance 信任顺序。
- `Unknown` 不是 false，analysis lead 不是 confirmed sink。
- `Next Action` 是当前 controller 推荐的单一 blocker；除非新证据冲突，应优先完成。

不要把某个任务的具体函数名、path 或动态状态写入 system prompt。

### Phase prompts

Phase prompt 只规定“如何消费当前 brief”，不重复 observation 中的数据：

- ingestion：调用 `analyze_description`，确认 harness，基于 verified ref 选择第一步。
- exploration：比较 Vulnerability Path 的 Top paths，READ endpoint/callsite，选择 active sink。
- investigation：沿 active path 解决第一个 `?` gate/mapping，避免重新广搜。
- formulation：把 Required Conditions 转成具体 PoC byte layout；unknown endian/offset 不得靠猜，应保留 seed 或做一次定向查询。
- verification：以 Experiments 的 oracle 反馈修正 path/gate/mapping；不要因一次 miss 清空所有 source-backed facts。

### Bug guidance

Bug guidance 只提供 crash-class 方法论（例如 buffer overflow 优先 length/destination），不能复制当前任务的动态 path/mapping，也不能宣称某 API 一定是漏洞。

## 9. Next Action 决策优先级

`_render_next_action()` 应从结构化 gap 中选择一个动作：

1. ready PoC → submit。
2. pending reflection/checkpoint → 完成协议动作。
3. submit feedback refuted 的 gate/mapping → 修复该条件。
4. active path 的第一个 unresolved reachability/dispatch gate。
5. sink 的第一个关键 input mapping gap。
6. 未选 active path → READ Top-1 endpoint/callsite 并确认/拒绝。
7. description/harness 尚未结构化 → ingestion action。
8. 无 blocker 且 conditions 足够 → 构造候选 PoC。

每个 Next Action 必须包含：一个推荐动作、一个具体目标（file:line/path_id/expression）和一个 stop condition。不要列 5 个同等优先的工具选项。

## 10. Prompt/Context 测试策略

除各任务的单元测试外，新增 `tests/test_vnext_context_rendering.py`，使用完整 state 场景测试最终 observation：

- 一级标题属于固定六段且顺序正确。
- 同一 verified ref/path/mapping 不跨 section 重复。
- 每条事实有标准 provenance。
- phase visibility、Top-K 和 TTL 生效。
- revision semantic event 强制 full brief；普通不变状态走 delta。
- compaction 后 full brief 能从 state 重建 active path 和 concrete mappings。
- renderer 不泄漏 raw dict、HTML escape、XML analysis block、fingerprint/revision。
- phase prompt 只引用实际存在的 section 名称。

新增 `scripts/audit_observation_context.py`（Task 05）扫描真实 trace：

- 一级标题违规数。
- 重复事实和永久 warning。
- 每 step observation token/char 分布。
- provenance 缺失率。
- delta/full refresh 比率。
- active path、first blocker、byte mapping 在 compact 前后的保真率。

