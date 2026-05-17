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

[快速开始](https://qitor.mintlify.app/zh/quickstart) · [教程课程](https://qitor.mintlify.app/zh/tutorials) · [基准测试](https://qitor.mintlify.app/zh/benchmarks/overview) · [CLI 参考](https://qitor.mintlify.app/zh/reference/cli) · [English README](README.md)

## v0.3.0 最新进展

- 正式引入 `RunSpec`、`ExperimentSpec` 与统一 benchmark 输出，强化可复现 run 基础设施。
- 新增 `qit bench` 官方工作流，覆盖 `run`、`eval`、`replay`、`export`。
- `qita` 支持 replay、export 与 diff，方便做 review 级轨迹分析。
- 教程课程与可复现 benchmark / failed-run replay 教程已补齐。
- desktop benchmark 现在完成了清晰分层：`desktop-starter` 继续作为官方 starter benchmark，`qitos.recipes.desktop.osworld_starter` 承载可复现 baseline recipe，而 `qitos.benchmark.osworld` 则开始承接真实 OSWorld 风格的 adapter/runtime/evaluator 集成。
- 现在整个 benchmark 面也完成了统一收口：GAIA、Tau-Bench、CyBench、`desktop-starter` 和 `osworld` 都开始通过 `qitos.benchmark` + `qitos.recipes` 路径运行，同时新增了第三方 benchmark 接入规范，方便未来继续扩展。

如果这个方向对你有帮助，欢迎 star、提 issue，或者直接参与贡献。

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

## 为什么是 QitOS

| 如果你想要... | QitOS 提供... |
|---|---|
| 可复现的 agent 研究 | 稳定的 `AgentModule + Engine` 内核 |
| 强可观测性 | `qita` board、replay、export 与 trace 工件 |
| benchmark 工作流 | GAIA、Tau-Bench、CyBench 适配 |
| 更少框架胶水 | 一条 canonical 执行主线 |

## 示例总览

### 核心模式

- **ReAct**：文本协议 + 每步一个动作的基线。
- **PlanAct**：先显式规划，再逐步执行。
- **Tree-of-Thought**：先分支，再选择，再行动。
- **Reflexion**：actor-critic 式带证据重试。

### 真实 Agent

- **Coding agent**：编辑器、shell、memory 组成的实用编码循环。
- **Research harness agent**：面向研究者的 prompt/parser/protocol 手写路径。
- **Desktop smoke**：最小、确定性的 desktop env 循环。

完整应用、产品 clone、网络安全 agent 与长工作流 showcase 应进入 `qitos-zoo`，而不是作为 QitOS core 的 canonical examples。

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

- 第一次接触： [简介](https://qitor.mintlify.app/zh/introduction)
- 第一条成功路径： [快速开始](https://qitor.mintlify.app/zh/quickstart)
- 安装方式： [Installation](https://qitor.mintlify.app/zh/installation)
- 写自己的最小 coding agent： [构建第一个 Agent](https://qitor.mintlify.app/zh/guides/build-your-first-agent)
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

QitOS 当前处于 **Alpha**。

- 相对稳定：`AgentModule + Engine`、trace/qita、canonical examples、benchmark adapters，以及官方可复现 run 契约。
- 仍会演进：更高层 convenience API、部分 `kit` 模块、实验性 toolset。
- 如果你正在评估接入，建议从 kernel 与 examples 开始，而不是假设所有高层表面都已冻结。

## 安装与版本

- 支持的 Python 版本：**3.10+**
- 普通用户安装：`pip install "qitos[models]"`
- 最小 coding agent：`qit demo minimal`
- 常见 provider 配置：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`QITOS_MODEL`
- 仅核心安装：`pip install qitos`
- 仓库源码安装：`pip install -r requirements.txt`
- 完整开发安装：`pip install -r requirements-dev.txt`
- 安装说明： [Installation](https://qitor.mintlify.app/zh/installation)

## 参与贡献

欢迎贡献 benchmark adapters、memory/history 工作流、qita UX 与核心框架能力。产品级 agent 应优先进入 `qitos-zoo`。开发环境、文档工作流和 PR 约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

MIT，见 [LICENSE](LICENSE)。
