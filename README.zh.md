# QitOS

![QitOS Logo](assets/logo.png)

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-qitor.github.io/qitos-0A66C2)](https://qitor.github.io/qitos/)
[![PyPI](https://img.shields.io/pypi/v/qitos.svg)](https://pypi.org/project/qitos/)
[![Repo](https://img.shields.io/badge/github-Qitor%2Fqitos-black)](https://github.com/Qitor/qitos)

**QitOS 是面向严肃智能体研发的 research-first 框架。**  
它提供统一执行内核、可组合模块与 benchmark 原生工作流，让你从想法快速走到可复现结果，而不必重写整套基础设施。

- English README: [README.md](README.md)
- 文档站点: [https://qitor.github.io/qitos/](https://qitor.github.io/qitos/)

## 界面预览

<table>
  <tr>
    <td align="center"><strong>QiTOS CLI</strong></td>
    <td align="center"><strong>qita Board</strong></td>
    <td align="center"><strong>qita 轨迹视图</strong></td>
  </tr>
  <tr>
    <td align="center">
      <a href="assets/qitos_cli_snapshot.png">
        <img src="assets/qitos_cli_snapshot.png" alt="QiTOS CLI" width="100%" />
      </a>
    </td>
    <td align="center">
      <a href="assets/qita_board_snapshot.png">
        <img src="assets/qita_board_snapshot.png" alt="qita Board" width="100%" />
      </a>
    </td>
    <td align="center">
      <a href="assets/qita_traj_snapshot.png">
        <img src="assets/qita_traj_snapshot.png" alt="qita Trajectory View" width="100%" />
      </a>
    </td>
  </tr>
</table>

## 为什么团队选择 QitOS

- **研究优先**：天然适配 ReAct、Plan-Act、ToT、Reflexion 及自定义 scaffolding 快速迭代。
- **统一内核**：`AgentModule + Engine`，生命周期稳定、可推理、易扩展。
- **模块化架构**：`core`、`engine`、`kit`、`benchmark`、`evaluate` 按需组合。
- **生态兼容**：可自然接入 OpenAI 兼容模型接口、主机环境与工具注册机制。
- **Benchmark 原生**：统一支持 GAIA、Tau-Bench、CyBench。
- **生产级可观测性**：trace、hooks、回放、导出由 `qita` 一体化提供。

## 核心优势

```text
Task -> Engine.run(...)
     -> prepare -> decide -> act -> reduce -> check_stop -> ...
     -> hooks + trace + replay + metrics
```

一套架构同时覆盖研究、评测与真实部署。

## 安装

```bash
pip install qitos
```

开发模式：

```bash
pip install -e .
pip install -e ".[models,yaml,benchmarks]"
```

在仓库根目录验证官方支持的测试集：

```bash
python -m pytest -q
```

## 快速开始

运行最小端到端链路：

```bash
python examples/quickstart/minimal_agent.py
```

运行模式化智能体示例：

```bash
export OPENAI_BASE_URL="https://api.siliconflow.cn/v1/"
export OPENAI_API_KEY="<your_api_key>"
python examples/patterns/react.py
```

查看轨迹与统计：

```bash
qita board --logdir runs
```

现在主示例都刻意保持为自包含脚本：

- 顶部常量直接定义 task、workspace 与模型默认值
- 常用组件统一从 `qitos.kit` 平铺导入
- 直接调用 `agent.run(...)`
- 终端 UI 与 trace 默认开启

## AgentModule + Engine 思维方式

QitOS 将职责拆分得非常清晰：

- `AgentModule`：策略层。定义状态、提示词、决策策略与 reduce 逻辑。
- `Engine`：执行层。统一驱动生命周期、工具调用、停止判定、trace 与 hooks。

这种分层让你可以专注提升 agent 智能，而不需要反复重造 runtime 基础设施。

## 最小 SWE Agent（需求到 PR）

```python
from dataclasses import dataclass, field
from typing import Any

from qitos import Action, AgentModule, Decision, StateSchema, ToolRegistry
from qitos.kit import EditorToolSet, MarkdownFileMemory, ReActTextParser, RunCommand

SWE_REACT_SYSTEM_PROMPT = """
你是资深软件工程师 Agent，目标是产出可直接提交 PR 的、满足需求的补丁。

每一步都必须使用 ReAct 格式：
Thought: 简洁说明下一步推理
Action: 仅输出一个可执行的工具调用

输出契约（必须严格遵守）：
Thought: <你的推理>
Action: <tool_name>(arg1="...", arg2="...")

规则：
- 修改前先阅读代码与上下文；
- 优先小步、可验证的改动；
- 每次修改后执行检查/测试；
- 如果失败，先定位根因再修复并复测；
- 所有动作必须基于当前 Observation，不得臆测。
""".strip()


@dataclass
class SWEState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    target_file: str = "buggy_module.py"
    test_command: str = 'python -c "import buggy_module; assert buggy_module.add(20, 22) == 42"'


class MinimalSWEAgent(AgentModule[SWEState, dict[str, Any], Action]):
    def __init__(self, llm: Any, workspace_root: str):
        reg = ToolRegistry()
        reg.include(EditorToolSet(workspace_root=workspace_root))
        reg.register(RunCommand(cwd=workspace_root))
        super().__init__(
            tool_registry=reg,
            llm=llm,
            model_parser=ReActTextParser(),
            memory=MarkdownFileMemory(path=f"{workspace_root}/memory.md"),
        )

    def init_state(self, task: str, **kwargs: Any) -> SWEState:
        return SWEState(task=task, max_steps=int(kwargs.get("max_steps", 12)))

    def build_system_prompt(self, state: SWEState) -> str | None:
        return SWE_REACT_SYSTEM_PROMPT

    def prepare(self, state: SWEState) -> str:
        return (
            f"Task: {state.task}\n"
            f"Target file: {state.target_file}\n"
            f"Test command: {state.test_command}\n"
            f"Step: {state.current_step}/{state.max_steps}"
        )

    def decide(self, state: SWEState, observation: dict[str, Any]):
        return None  # Engine 默认模型路径：prepare -> llm -> parser

    def reduce(self, state: SWEState, observation: dict[str, Any], decision: Decision[Action]) -> SWEState:
        results = observation.get("action_results", [])
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {decision.actions[0]}")
        if results:
            state.scratchpad.append(f"Observation: {results[0]}")
            if isinstance(results[0], dict) and int(results[0].get("returncode", 1)) == 0:
                state.final_result = "需求已实现，且验证通过。"
        state.scratchpad = state.scratchpad[-40:]
        return state


# llm = ...
# agent = MinimalSWEAgent(llm=llm, workspace_root="./playground")
# result = agent.run(
#     task="实现需求并让检查通过。",
#     workspace="./playground",
#     max_steps=12,
#     return_state=True,
# )
# print(result.state.final_result)
# print(result.state.stop_reason)
```

`agent.run(...)` 是推荐主线。默认就会提供：

- 终端 render
- 写入 `runs/` 的 trace 工件
- 当传入 `workspace=...` 时自动写入本地 render event 日志

## 提示词-解析器契约（关键）

提示词输出格式与 parser 必须一一对应。这不是风格问题，而是运行正确性的前提。

- `ReActTextParser` 期望模型输出 `Thought:` 与 `Action:` 文本格式。
- 如果改成 XML 输出，必须同时切换到 XML parser，并在系统提示词中强制 XML 标签。
- 如果改成 JSON 输出，必须切换 JSON parser，并在提示词中约束 JSON 结构。
- 只改提示词或只改 parser 都会导致解析不稳定。

快速映射：

- ReAct 文本提示词 -> `ReActTextParser`
- XML 提示词（`<think>...</think><action>...</action>`）-> `XML parser`
- JSON 提示词（`{"thought":"...","action":{...}}`）-> `JSON parser`

## 你可以构建什么

模式示例：
- `examples/patterns/react.py`
- `examples/patterns/planact.py`
- `examples/patterns/tot.py`
- `examples/patterns/reflexion.py`

真实场景：
- `examples/real/coding_agent.py`
- `examples/real/swe_agent.py`
- `examples/real/computer_use_agent.py`
- `examples/real/epub_reader_agent.py`

## Benchmark 与评测

QitOS 统一链路：

`数据样本 -> adapter -> Task -> Engine -> evaluate -> metric report`

内置适配：
- `qitos.benchmark.gaia`
- `qitos.benchmark.tau_bench`
- `qitos.benchmark.cybench`

评测体系：
- `qitos.evaluate` 负责单任务结果判定
- `qitos.metric` 负责基准级指标汇总
- `qitos.kit` 提供 rule-based / DSL-based / model-based evaluator 与常用 metrics

## qita 可观测性

- `qita board`：运行总览与统计
- `qita view`：结构化轨迹查看
- `qita replay`：执行过程回放
- `qita export`：导出 JSON / HTML 工件

## 项目结构

- `qitos/core/`：接口与核心契约
- `qitos/engine/`：执行内核
- `qitos/kit/`：可复用模块（工具、解析、规划、记忆、评测）
- `qitos/benchmark/`：benchmark 适配层
- `qitos/qita/`：轨迹工具链

## 文档

- 主文档: [https://qitor.github.io/qitos/](https://qitor.github.io/qitos/)
- API 参考: [https://qitor.github.io/qitos/reference/api_generated/](https://qitor.github.io/qitos/reference/api_generated/)
- 中文文档: [https://qitor.github.io/qitos/zh/](https://qitor.github.io/qitos/zh/)

## License

MIT，见 [LICENSE](LICENSE)。
