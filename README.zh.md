# QitOS

<img src="assets/logo.png" alt="QitOS Logo" width="75%">

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-qitor.github.io/qitos-0A66C2)](https://qitor.github.io/qitos/)
[![PyPI](https://img.shields.io/pypi/v/qitos.svg)](https://pypi.org/project/qitos/)
[![Repo](https://img.shields.io/badge/github-Qitor%2Fqitos-black)](https://github.com/Qitor/qitos)

面向可复现 LLM Agent 的 research-first 框架。

QitOS 提供清晰的 `AgentModule + Engine` 内核、benchmark-ready 工作流，以及内建的 `qita` 运行可观测能力。

[开始上手](https://qitor.github.io/qitos/zh/start-here/) · [10 分钟教程](https://qitor.github.io/qitos/zh/getting-started/build_agent_in_10_minutes/) · [示例总览](https://qitor.github.io/qitos/tutorials/examples/) · [English README](README.md)

## Live Terminal of QitOS for Code Review

<p align="center">
  <img src="demo.gif" alt="QitOS long-running agent demo" width="92%">
</p>

## QitOS 适合谁

- **研究者**：用可复现运行快速试验 ReAct、PlanAct、ToT、Reflexion 和新方法。
- **Agent 构建者**：在稳定执行循环上构建工具型 agent，而不是先写一堆框架胶水。
- **评测使用者**：用与真实 agent 相同的内核跑 GAIA、Tau-Bench、CyBench。

## 2 分钟跑起来

在仓库根目录执行：

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="<your_api_key>"
python examples/quickstart/minimal_agent.py
qita board --logdir runs
```

接下来可以继续：

- 想看 ReAct：见 [`examples/patterns/react.py`](examples/patterns/react.py)
- 想看 coding agent：见 [`examples/real/coding_agent.py`](examples/real/coding_agent.py)
- 想看 benchmark：从 [评测指南](https://qitor.github.io/qitos/zh/builder/benchmark_gaia/) 开始

## 为什么是 QitOS

| 如果你想要... | QitOS 提供... |
|---|---|
| 可复现的 agent 研究 | 稳定的 `AgentModule + Engine` 内核 |
| 强可观测性 | `qita` board、replay、export 与 trace 工件 |
| benchmark 工作流 | GAIA、Tau-Bench、CyBench 适配 |
| 更少框架胶水 | 一条 canonical 执行主线 |

## 最小 Agent 形状

```python
from dataclasses import dataclass
from typing import Any

from qitos import Action, AgentModule, Decision, StateSchema


@dataclass
class DemoState(StateSchema):
    pass


class DemoAgent(AgentModule[DemoState, dict[str, Any], Action]):
    def init_state(self, task: str, **kwargs: Any) -> DemoState:
        return DemoState(task=task, max_steps=6)

    def build_system_prompt(self, state: DemoState) -> str | None:
        return "Solve the task step by step."

    def prepare(self, state: DemoState) -> str:
        return f"Task: {state.task}\nStep: {state.current_step}/{state.max_steps}"

    def decide(self, state: DemoState, observation: dict[str, Any]):
        return None

    def reduce(self, state: DemoState, observation: dict[str, Any], decision: Decision[Action]) -> DemoState:
        return state
```

完整的 coding agent 与 SWE walkthrough 请看：

- [10 分钟搭一个 Agent](https://qitor.github.io/qitos/zh/getting-started/build_agent_in_10_minutes/)
- [Coding Agent Walkthrough](https://qitor.github.io/qitos/tutorials/examples/real_coding/)
- [SWE Agent Walkthrough](https://qitor.github.io/qitos/tutorials/examples/real_swe/)

## 示例总览

### 核心模式

- **ReAct**：文本协议 + 每步一个动作的基线。
- **PlanAct**：先显式规划，再逐步执行。
- **Tree-of-Thought**：先分支，再选择，再行动。
- **Reflexion**：actor-critic 式带证据重试。

### 真实 Agent

- **Coding agent**：编辑器、shell、memory 组成的实用编码循环。
- **SWE agent**：更强的规划型软件工程工作流。
- **Computer-use agent**：偏网页研究与 computer-use 风格。
- **EPUB reader**：文档驱动、带分支推理的阅读 agent。

### 评测

- **GAIA**：运行在 QitOS 内核上的 benchmark runner。
- **Tau-Bench**：标准 benchmark adapter 链路。
- **CyBench**：带 guided metrics 的 CTF 式评测。

canonical examples 目录：

- [`examples/quickstart/`](examples/quickstart/)
- [`examples/patterns/`](examples/patterns/)
- [`examples/real/`](examples/real/)
- [`examples/benchmarks/`](examples/benchmarks/)

## 文档地图

- 第一次接触： [Start Here](https://qitor.github.io/qitos/zh/start-here/)
- 第一条成功路径： [快速上手](https://qitor.github.io/qitos/zh/getting-started/)
- 写自己的 agent： [10 分钟搭一个 Agent](https://qitor.github.io/qitos/zh/getting-started/build_agent_in_10_minutes/)
- 理解运行时： [Kernel](https://qitor.github.io/qitos/zh/research/kernel/)
- 理解框架契约： [Contracts & Guarantees](https://qitor.github.io/qitos/zh/reference/contracts/)
- 看典型场景： [Use Cases](https://qitor.github.io/qitos/zh/use-cases/)
- 看 walkthrough： [Example Walkthroughs](https://qitor.github.io/qitos/tutorials/examples/)
- 看 benchmark： [GAIA](https://qitor.github.io/qitos/zh/builder/benchmark_gaia/) / [Tau-Bench](https://qitor.github.io/qitos/zh/builder/benchmark_tau/)
- 看 API： [API 参考](https://qitor.github.io/qitos/zh/reference/api_generated/)

## 界面预览

<table>
  <tr>
    <td align="center"><strong>QitOS CLI</strong></td>
    <td align="center"><strong>qita Board</strong></td>
    <td align="center"><strong>qita Trajectory View</strong></td>
  </tr>
  <tr>
    <td align="center">
      <a href="assets/qitos_cli_snapshot.png">
        <img src="assets/qitos_cli_snapshot.png" alt="QitOS CLI" width="100%" />
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

## 当前阶段

QitOS 当前处于 **Alpha**。

- 相对稳定：`AgentModule + Engine`、trace/qita、canonical examples、benchmark adapters。
- 仍会演进：更高层 convenience API、部分 `kit` 模块、实验性 toolset。
- 如果你正在评估接入，建议从 kernel 与 examples 开始，而不是假设所有高层表面都已冻结。

## 安装与版本

- 支持的 Python 版本：**3.9+**
- 普通用户安装：`pip install qitos`
- 仓库快速安装：`pip install -r requirements.txt`
- 完整开发安装：`pip install -r requirements-dev.txt`
- 安装说明： [Installation](https://qitor.github.io/qitos/zh/getting-started/installation/)

## 参与贡献

欢迎贡献。开发环境、文档工作流和 PR 约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

MIT，见 [LICENSE](LICENSE)。
