# 00 — 固定边界、基线与验收不变量

优先级：P0  
依赖：无  
代码改动：原则上无；只允许新增测试 fixture/基线说明  
禁止：任何本地 fuzz、GDB/LLDB、instrumentation、本地目标执行

## 最新再审计状态

状态：**部分完成**。

- HEAD 仍为 `6a00a33`，当前分支领先远端 3 commits；
- agent 工作树有 8 个已跟踪文件修改，并新增 `agent_prompts/mode/`；
- 当前测试基线是 `10 passed, 3 failed`，详见 [CURRENT_STATE.md](CURRENT_STATE.md)；
- 24-task smoke manifest 尚未落地；
- `../qitos` 自身有 4 个未提交 parser/render 文件，后续不得覆盖。

本任务完成标准更新为：把上述事实写入实际 baseline artifact，并创建无答案泄漏的 manifest；不能只引用本页。

## 目标

在改代码前固定“V2 要解决什么”和“不能靠什么解决”，避免后续 coding agent 把任务扩展成动态分析平台或重新实现框架。

## 必须先阅读

- `AGENTS.md`
- `issues/010-cybergym-agent-architecture-audit-2026-06-29.md`
- `issues/011-cybergym-task-solving-effectiveness-2026-06-29.md`
- `issues/012-anthropic-microsoft-lessons-for-cybergym-agent-2026-06-29.md`
- `docs/superpowers/specs/2026-06-20-cybergym-p0-p1-lightweight-design.md`

旧 lightweight design 的“收紧现有结构、不扩张 runtime”原则继续有效；其中与本任务清单冲突的 cleanroom verifier、dynamic worker 或 fuzz 设想全部不实施。

## 步骤

### 1. 记录当前代码状态

保存但不要提交运行产物：

```bash
git rev-parse --short HEAD
git status --short
git diff --stat
```

特别检查用户已修改的：

- `state.py`
- `agent_impl/phase.py`
- `agent_impl/state_init.py`
- `agent_impl/observations.py`
- `agent_impl/validation.py`

任务实现必须在这些改动之上做最小 patch，不能用旧版本整文件覆盖。

### 2. 运行并记录基线测试

```bash
PYTHONPATH=../qitos:.. python -m pytest tests -q
```

记录每个既有失败，不允许把既有失败写成新任务“预期通过”。当前三个失败按以下方式处理：phase shape 和 layered schema 在任务 07 做语义迁移；stop-semantics 在任务 06 修实现。本任务不临时改测试。

### 3. 固定 24-task smoke manifest

如果仓库已有 manifest，复用并补充分层；没有时只新增一个 JSON/JSONL manifest，不复制 task workspace 或 PoC。分层建议：

- 4 个描述包含明确函数/文件；
- 4 个描述模糊、需 repo localization；
- 4 个文本/grammar 输入；
- 4 个常见二进制结构输入；
- 4 个 seed mutation 候选任务；
- 4 个生命周期/状态/复杂 carrier 任务。

每条只记录 `task_id`、分层标签和选择理由。不要记录答案、patch、成功 PoC 或 hidden fix-side 信息。

### 4. 固定全局禁区

在所有 task/PR 描述中复制以下检查项：

```text
[ ] 未新增 fuzz engine/coverage loop
[ ] 未新增 gdb/lldb/rr/调试器
[ ] 未新增本地 target build/run/verifier
[ ] 未新增外部 corpus/联网 seed
[ ] 未新增 benchmark-time 多 agent runtime
[ ] 动态事实只来自 submit_poc
```

### 5. 固定成功口径

运行时 agent 只能看到 public vul-side 反馈：

- `vul_exit_code != 0` 或 server 明确 crash：agent 成功并停止；
- `vul_exit_code == 0`：no-crash，本次 candidate 未触发；
- submission error/timeout/duplicate：基础设施或提交事件；
- fix-side 数据：不得进入 prompt、策略或运行时 stop 判断。

离线 benchmark 可以另记 strict evaluator score，但不能反馈给 agent。

## 验收

- 基线 commit、dirty files、测试结果已记录；
- 24-task manifest 不含答案泄漏；
- 所有后续任务明确引用本文件禁区；
- 没有为了让 baseline 变绿而修改业务逻辑或放宽测试。

## 不要做

- 不创建新架构总图；010-012 已经足够。
- 不复制一份 agent 到 `v2/`；仍在当前实现上演进。
- 不提前修改 stop、phase 或 prompt；分别属于后续任务。
