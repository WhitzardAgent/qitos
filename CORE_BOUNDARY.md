# QitOS Core Boundary

QitOS is the kernel-first research framework. The main repository should stay small, torch-like, and centered on the reusable agent runtime. Product-grade applications and showcase agents belong in `qitos-zoo`.

## Stable Core Framework Surface

The stable surface is:

- `qitos.core`
- `qitos.engine`
- `qitos.trace`
- `qitos.qita`
- basic model/provider abstractions needed by the engine
- public contracts such as `AgentModule`, `StateSchema`, `Decision`, `Action`, `BaseTool`, `ToolRegistry`, trace artifacts, run specs, and hook contracts

Top-level `qitos` imports should stay limited to these kernel contracts and compatibility-critical public contracts.

## Curated Framework Extensions

The following may remain in this repository when they are generic, reusable, and not tied to one product agent:

- `qitos.kit`
- `qitos.kit.tool`
- `qitos.kit.toolset`
- `qitos.prompting`
- `qitos.protocols`
- `qitos.models`
- `qitos.render`

These APIs are useful framework extensions, but they are not all equally stable. They should avoid product naming, product workflows, and high-risk default exports.

## Recipes And Benchmarks

`qitos.recipes` may contain reusable research baselines or canonical benchmark methods. Recipes should be callable from thin examples instead of duplicated inside examples.

`qitos.benchmark` and `qitos.recipes.benchmarks` may contain framework-level adapters, runners, scorers, and dataset-neutral glue. They must not vendor benchmark datasets, large external assets, or product-specific workflows.

## Examples Policy

`examples/` is a small canonical learning path, not an app gallery. Examples should:

- teach one QitOS concept at a time
- run locally or with one standard model-provider path
- be short enough to read as documentation
- demonstrate canonical authoring flow
- avoid heavy hidden dependencies
- avoid standalone product behavior

Full applications live in `qitos-zoo`.

## qitos-zoo

Move or prepare to move examples and apps that:

- reproduce mature agent products
- require many files, configs, prompts, workflows, or assets
- are showcase-grade instead of teaching-first
- are cybersecurity/pentesting product agents
- are Claude Code-style product agents
- have their own roadmap, UI, CLI, benchmark harness, or workflow state

Recommended names:

- `qitos-coder`: Claude Code-inspired coding agent built with QitOS.
- `qitos-cyber-agent`: PentAGI-inspired cybersecurity agent built with QitOS.

## Security-Sensitive Rule

Cybersecurity research tooling must never be part of the default public surface. It may exist only as explicit experimental modules with clear warnings, or as qitos-zoo applications with controlled documented use.

Do not export offensive or high-risk tools from `qitos.__init__`, `qitos.kit` default imports, `qit demo`, or quickstart examples.

## Promotion Rule

Code may move from qitos-zoo into QitOS only if it is generic, tested, documented, and needed by at least two independent apps. Product-specific code stays in qitos-zoo.

## Decision Table

| Item | Belongs in core? | Decision |
| --- | --- | --- |
| Engine loop | Yes | Stable core |
| Agent contracts | Yes | Stable core |
| Trace/qita artifacts | Yes | Stable observability |
| Generic tool protocol | Yes | Stable core or kit |
| `CodingToolSet` | Maybe | Keep only if generic and minimal |
| Claude Code-style agent | No | qitos-zoo |
| PentAGI-style cyber agent | No | qitos-zoo |
| SWE product workflow | No | qitos-zoo unless reduced to minimal recipe |
| OpenAI CUA product clone | No | qitos-zoo unless reduced to minimal desktop smoke test |
| Benchmark adapters | Yes | Only if thin and dataset-neutral |
| Benchmark datasets/assets | No | External or qitos-zoo |
| Experimental security tools | Opt-in only | Never default-exported |
