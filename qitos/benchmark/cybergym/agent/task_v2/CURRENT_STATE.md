# V2 再审计：当前更新与任务差异

日期：2026-06-29  
分支：`para_action`  
HEAD：`6a00a33`（本地分支领先远端 3 commits）  
范围：HEAD 之后当前未提交工作树 + `../qitos` 当前状态

## 1. 当前新增内容

工作树修改：

- `state.py`：新增 `agent_mode`、`chain_completeness_score()`、`reorder_chain_nodes()`、`sync_phase_from_mode()`；
- `agent_impl/phase.py`：四 phase 改成 `chain_construction / poc_iteration`；
- `agent_impl/validation.py`：chain mode 下读预算放开，但按 completeness 硬限制 WRITE/BASH/submit；
- `agent_impl/prompts.py`：开始按两模式组装 prompt；
- `agent_prompts/mode/chain_construction.md`、`poc_iteration.md`：新增模式 prompt；
- `agent_impl/observations.py`：新增 Chain Work Order，同时继续在 render 中更新 suggestion/gate；
- `agent_impl/feedback.py`：增强所谓 path frontier 推断，并会向前降级 gate；
- `agent.py`：同步 mode/phase，step 12 强制进入 poc iteration，扩展 constraint extraction。

这些改动提供了可复用的两模式骨架，但没有解决 010-012 中的事实契约问题。

## 2. 最新测试基线

命令：

```bash
PYTHONPATH=../qitos:.. python -m pytest tests -q
```

结果：`10 passed, 3 failed`。

| 失败 | 性质 | 正确处理 |
|---|---|---|
| `test_phase_engine_shape` | 有意架构迁移导致旧断言过期 | 任务 07 更新为两模式语义测试，不恢复四 phase |
| `test_vul_side_stop_criteria` | 真实语义缺陷 | 任务 06 修实现；不能把测试改成“不停止” |
| `test_layered_tool_schema_keeps_basic_tools_and_delays_advanced` | mode 初始化/legacy phase 兼容冲突 | 任务 07 明确 schema 只由 artifact/mode 派生后更新测试 |

## 3. 最新实现中的阻断性问题

### 3.1 Result contract 仍未修

- QitOS `ActionResult` 虽有 `action_id`，但 runtime context 没有；
- `_ActionRuntime` 生成 `ToolResult` 时仍不保留 action id；
- agent 仍使用 `_last_structured_output`；
- submit 仍使用 `_last_submit_structured_output`；
- QitOS hardcoded concurrency-safe set 仍包含 `submit_poc`。

结论：任务 01 仍是绝对 P0，不能先评估新两模式收益。

### 3.2 Chain completeness 是可被模型填表满足的分数

当前分数依赖：entry/sink 是否存在、node/gate 是否标 `confirmed`、是否有 format gate。模型和 render 都能制造这些状态，但没有强制 source evidence。

`reorder_chain_nodes()` 的 docstring 声称使用 call graph，实际只按 role 和旧 order 排序。它会重编号 gate 所依赖的 node order，且没有同步证明 edge 关系。

结论：任务 04 不应继续调权重；应先引入 evidence invariant，再决定是否保留只读 completeness summary。

### 3.3 Observation 仍然不是纯函数

`_constraint_board_lines()` 当前会：

- prune expired suggestions；
- 增加 `observation_count`；
- 自动 promotion suggestion 为 gate；
- 修改 `suggested_constraints` 和 `call_chain_gates`。

prepare 在不同路径可能多次调用 render，因此相同事实会因“被展示次数”而升级。

结论：任务 04/07 必须把这些 mutation 移到 reducer；展示次数永远不能作为 evidence。

### 3.4 Feedback 推断比此前更激进

`NO_CRASH -> path_not_reached` 的旧错误仍在。新逻辑又增加：

- 用历史 `crash_location`/raw output 猜 reached node；
- refute 所谓 frontier gate；
- 将更早的 confirmed path/dispatch gates 降级；
- 无 frontier 时仍 refute 最早 open gate。

普通 no-crash 没有 crash location，且历史 location 可能来自别的 candidate；这些推断不能作为当前 candidate 的路径证据。

结论：任务 06 第一阶段应删除整段 frontier/back-propagation，而不是继续改匹配规则。

### 3.5 两模式控制面存在硬锁和双真相源

当前同时存在：

```text
agent_mode
current_phase
control_mode
runtime_stage
candidate_required
pending checkpoints
chain_completeness_score
proximity_score
```

具体冲突：

- chain mode completeness < 0.3 时硬阻止 WRITE/BASH/submit；
- prompt 明确要求“不要构造 PoC”；
- step 12 又强制切换并要求首次提交；
- phase engine、`agent.py` 和 `sync_phase_from_mode()` 都会写 mode/phase；
- no-crash 后又可能根据 proximity 返回 chain mode。

结论：任务 07 保留两个 mode 名称，但必须删除 completeness/action hard gate、step 强制真值和 proximity transition。

### 3.6 新 prompt 仍包含已否定策略

`agent_prompts/mode/poc_iteration.md` 当前包含：

- seed 存在时 `ALWAYS` corpus-first；
- “每次 miss 都能告诉你哪个 gate 错”；
- `path_not_reached`、`discriminant_failed`、`vul_only_triggered` 维修路线；
- public crash 后继续“提高 precision”。

`chain_construction.md` 当前要求完整 entry-to-sink chain 和 score 阈值后才能写 PoC，也与早候选、软压力原则冲突。

结论：这两个文件是任务 07 的直接修改目标，不能当作已完成 prompt。

## 4. QitOS 与 bundled copy 状态

`../qitos` 当前另有未提交修改：

- `qitos/engine/parser.py`
- `qitos/render/_hooks_impl.py`
- `qitos/render/cli_render.py`
- `qitos/render/content_renderer.py`

这些看起来属于 parser/render 工作，不是 V2 result contract。实现任务 01 时必须做局部 patch，不得覆盖这些用户改动。

开发源码与 `../qitos/qitos/benchmark/cybergym/agent` 仍在多个受管文件上不同，且 bundled copy 尚无 `agent_prompts/mode/`。任务 08 前不要手工双写；以本仓库为 source of truth，最终统一运行同步脚本。

## 5. 更新后的立即执行顺序

1. **01 Result Contract**：否则无法信任任何新模式对并行 action/submit 的效果。
2. **06 Oracle 最小止血部分**：立即停止 no-crash refute/back-propagation，并修 vul-crash stop。
3. **07 Observation purity 最小止血部分**：先移除 render mutation 和 chain hard lock。
4. **02 Bootstrap Precision**。
5. **03 Static Scope/Harness Contract**。
6. **04 Evidence Chain**：把当前 chain 骨架改造成证据模型。
7. **05 Candidate Experiment**。
8. **07 其余 control/prompt 收敛**。
9. **08 Evaluation/Sync**。

这里把 06/07 的“止血子步骤”提前，是因为当前未提交改动已引入会直接污染 state 和阻止 PoC 的行为；不代表跳过它们的完整依赖和验收。

## 6. 本轮仍保持的禁区

- 不新增或调用本地 fuzzing；
- 不新增 GDB/LLDB/rr；
- 不新增 coverage/instrumentation；
- 不构建或运行本地 target；
- 不新增 local verifier/container；
- 不使用外部 corpus/历史 PoC；
- 动态事实仍只来自官方 `submit_poc`。

