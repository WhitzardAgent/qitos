# QitOS

<img src="assets/logo.png" alt="QitOS Logo" width="75%">

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-qitor.mintlify.app-0A66C2)](https://qitor.mintlify.app/)
[![PyPI](https://img.shields.io/pypi/v/qitos.svg)](https://pypi.org/project/qitos/)
[![Repo](https://img.shields.io/badge/github-Qitor%2Fqitos-black)](https://github.com/Qitor/qitos)

QitOS 是面向 agent 研究者的 torch-flavor 框架。

你可以在同一个 `AgentModule + Engine` 内核上原型化方法、运行 benchmark，并用内建 `qita` 检查长时轨迹。

QitOS 主仓库是小而清晰的核心框架。产品级 / 展示级应用会进入独立的 `qitos-zoo`，包括计划中的 `qitos-coder` 与 `qitos-cyber-agent`。

[快速开始](https://qitor.mintlify.app/zh/quickstart) · [教程课程](https://qitor.mintlify.app/zh/tutorials) · [基准测试](https://qitor.mintlify.app/zh/benchmarks/overview) · [CLI 参考](https://qitor.mintlify.app/zh/reference/cli) · [更新日志](CHANGELOG.md) · [English README](README.md)

## 最新进展

- **MCP Streamable HTTP 兼容性**：`MCPServerStreamableHttp` 现在会跟随 gateway 重定向，并解析 SSE `data:` 帧中封装的 JSON-RPC 响应。
- **原生工具 schema 加固**：OpenAI-compatible `tools=` payload 不再为 `Any` 或 `**kwargs` 参数导出非法的 `type: any` schema。
- **ReAct 解析兼容性**：`ReActTextParser` 现在可以解析部分 OpenAI-compatible 模型常见的 `Action Input`、XML 风格 action 标签和围栏 JSON tool-call 变体。

## v0.8.0 最新进展

- **架构清洁稳定版**：v0.8.0 记录包边界、超大文件热点、可选依赖边界和发布守护规则，方便贡献者判断代码归属。
- **更干净的公共面**：默认导出继续聚焦 `AgentModule + Engine` 内核和通用 kit 构建块。
- **安全工具显式 opt-in**：安全审计 builder 通过 `qitos.kit.toolset.security_audit` 或 `qitos.kit.tool.experimental.security_research` 等显式路径使用。
- **Workflow 可选导入**：`qitos.workflow` 现在是懒加载 facade，核心安装不再因为导入 workflow 包而强制需要 `qitos-dag`。
- **边界回归测试**：公共 API、kit/toolset 默认导出、workflow 可选导入和 core 依赖方向都有测试守护。

详见 [CHANGELOG.md](CHANGELOG.md)。

## Live Terminal of QitOS for Code Review

<p align="center">
  <img src="demo.gif" alt="QitOS long-running agent demo" width="92%">
</p>

## QitOS 适合谁

- **方法研究者**：频繁改 prompt、parser、critic、tool 与 memory policy，但不想每次都重写 runtime。
- **benchmark 使用者**：希望 GAIA、Tau-Bench、CyBench 跑在和 agent 开发同一套内核上。
- **长时 agent 调试者**：更关心 trajectory review、replay、diff 与 context collapse，而不是先拼应用脚手架。

## 2 分钟跑通 QitOS

QitOS 里的 minimal agent 应该是一个最轻量的 **coding agent**。它会配置真实模型、进入 workspace、改代码、跑验证，并留下 qita 可检查的 trace。

```bash
pip install "qitos[models]"
export OPENAI_API_KEY="sk-..."
qit --version
qit demo minimal
qita board --logdir runs
```

OpenAI-compatible provider 常见补充配置：

```bash
export OPENAI_BASE_URL="https://api.siliconflow.cn/v1/"
export QITOS_MODEL="Qwen/Qwen3-8B"
```

`qit demo minimal` 会先种一个最小 bug workspace，再让模型驱动的 coding agent 修复它、运行验证，并把轨迹写到 `./runs`。

接下来可以继续：

- 想看 ReAct：见 [`examples/patterns/react.py`](examples/patterns/react.py)
- 想看 coding agent：见 [`examples/real/coding_agent.py`](examples/real/coding_agent.py)
- 想看 benchmark：从 [评测总览](https://qitor.mintlify.app/zh/benchmarks/overview) 开始
- 想看方法模板：见 [方法模板指南](https://qitor.mintlify.app/zh/guides/method-templates)

## 为什么是 QitOS

| 如果你想要... | QitOS 提供... |
|---|---|
| 可复现的 agent 研究 | 稳定的 `AgentModule + Engine` 内核 |
| 方法 = Agent + Critic | 12 个内建方法模板，映射经典论文 |
| 强可观测性 | `qita` board、replay、export 与 trace 工件 |
| benchmark 工作流 | GAIA、Tau-Bench、CyBench 适配器 |
| 更少框架胶水 | 一条 canonical 执行主线 |

## 方法模板

QitOS 内置 12 个方法模板 — 每个都是实现经典 agentic 推理模式的 Agent + Critic 组合：

| 模板 | 模式 | 论文 |
|------|------|------|
| ReAct | 推理 + 行动 | Yao et al. 2023 |
| PlanAct | 先规划再执行 | — |
| SWE-Agent | 软件工程 | Princeton 2024 |
| Voyager | 开放探索 | Wang et al. 2023 |
| Debate | 多 Agent 辩论 | — |
| Manager-Worker | 编排与委派 | — |
| Planner-Executor | 计划分解 | — |
| Self-Refine | 生成 → 批评 → 改进 | Madaan et al. 2023 |
| Reflexion | 行动 → 反思 → 重试 | Shinn et al. 2023 |
| LATS | 蒙特卡洛树搜索 | Zhou et al. 2023 |
| MoA | 并行提议 + 聚合 | Wang et al. 2024 |
| Magentic-One | 编排器 + 专家 | Furtado et al. 2024 |

直接使用：

```python
from qitos.recipes.reflexion import ReflexionAgent, ReflexionCritic

agent = ReflexionAgent(llm=my_llm)
result = agent.run(
    task="Debug the failing test",
    critics=[ReflexionCritic(max_reflections=3)],
    max_steps=15,
    return_state=True,
)
```

或从任意模板脚手架新 agent：

```bash
pip install qitos[cookiecutter]
qit new --agent-name my_agent --agent-description "My custom agent"
qit list-templates
```

## 工具层布局

QitOS 将工具导入分为三层：

- `qitos.kit`：最简单的常用工具集入口
- `qitos.kit.toolset`：场景导向的预设和注册表构建器
- `qitos.kit.tool.<domain>`：高级原子能力导入

默认组合是列表优先：

```python
from qitos import ToolRegistry
from qitos.kit.tool.file import ReadFile
from qitos.kit.toolset import coding_tools

registry = ToolRegistry().include_toolset(
    [
        ReadFile(workspace_root="."),
        coding_tools(workspace_root="."),
    ]
)
```

安全敏感工具为显式 opt-in 导入，不在 `qitos`、`qitos.kit`、`qit demo` 或快速开始路径中。

## 文档地图

- 第一次接触： [简介](https://qitor.mintlify.app/zh/introduction)
- 第一条成功路径： [快速开始](https://qitor.mintlify.app/zh/quickstart)
- 安装方式： [安装](https://qitor.mintlify.app/zh/installation)
- 写自己的最小 coding agent： [构建第一个 Agent](https://qitor.mintlify.app/zh/guides/build-your-first-agent)
- 方法模板： [方法模板指南](https://qitor.mintlify.app/zh/guides/method-templates)
- 理解运行时： [AgentModule](https://qitor.mintlify.app/zh/concepts/agent-module) / [Engine](https://qitor.mintlify.app/zh/concepts/engine)
- 看 trace： [可观测性](https://qitor.mintlify.app/zh/guides/observability)
- 走完整课程： [教程](https://qitor.mintlify.app/zh/tutorials)
- 看 benchmark： [评测总览](https://qitor.mintlify.app/zh/benchmarks/overview)
- 看命令： [CLI 参考](https://qitor.mintlify.app/zh/reference/cli)
- 看 API： [API 参考](https://qitor.mintlify.app/zh/reference/api)

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

QitOS 当前处于 **Beta**。

- 相对稳定：`AgentModule + Engine`、trace/qita、canonical examples、benchmark adapters，以及官方可复现 run 契约。
- 仍会演进：更高层 convenience API、部分 `kit` 模块、实验性 toolset。
- 如果你正在评估接入，建议从 kernel 与 examples 开始，而不是假设所有高层表面都已冻结。
- 持续演进和升级说明见 [CHANGELOG.md](CHANGELOG.md)。

## 安装与版本

- 支持的 Python 版本：**3.10+**
- 普通用户安装：`pip install "qitos[models]"`
- 版本检查：`qit --version`
- 最小 coding agent：`qit demo minimal`
- 常见 provider 配置：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`QITOS_MODEL`
- 仅核心安装：`pip install qitos`
- 仓库源码安装：`pip install -r requirements.txt`
- 完整开发安装：`pip install -r requirements-dev.txt`
- 可选扩展：`qitos[wandb]`、`qitos[mlflow]`、`qitos[cookiecutter]`、`qitos[all]`
- 安装说明： [安装](https://qitor.mintlify.app/zh/installation)

## 参与贡献

欢迎贡献方法模板、benchmark adapters、memory/history 工作流、qita UX 与核心框架能力。产品级 agent 应优先进入 `qitos-zoo`。开发环境、方法模板贡献、文档贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

MIT，见 [LICENSE](LICENSE)。
