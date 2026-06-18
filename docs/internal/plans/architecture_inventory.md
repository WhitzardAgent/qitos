# Architecture Inventory for v0.8.0

## Snapshot

This inventory supports the v0.8.0 architecture-cleanliness release. It records the current repository boundary map and the cleanup decisions that should stay visible to maintainers.

| Area | Packages | Classification |
| --- | --- | --- |
| Stable kernel | `qitos.core`, `qitos.engine`, `qitos.trace`, `qitos.qita` | Core contracts, runtime, trace artifacts, inspection UI |
| Framework extensions | `qitos.kit`, `qitos.models`, `qitos.protocols`, `qitos.prompting`, `qitos.render`, `qitos.harness` | Generic implementations and adapters |
| Research workflows | `qitos.recipes`, `qitos.benchmark`, `qitos.evaluate`, `qitos.metric`, `qitos.experiment` | Baselines, benchmark adapters, evaluation |
| Runtime support | `qitos.cache`, `qitos.checkpoint`, `qitos.config`, `qitos.debug`, `qitos.mcp`, `qitos.hf`, `qitos.leaderboard` | Optional or operational support |
| Optional graph workflow | `qitos.workflow` | Optional `qitos-dag` integration |
| Product boundary | `qitos_zoo`, `docs/internal/plans/qitos_zoo_migration` | Destination for product-like agents |

## Large-File Hotspots

Files above the cleanup threshold are not automatically wrong, but each needs an ownership note before refactoring:

| File family | Current reason to tolerate size | Follow-up direction |
| --- | --- | --- |
| `qitos/benchmark/tau_bench/port/envs/*/tasks*.py` | Ported benchmark task data | Keep out of core; consider external dataset packaging later |
| `qitos/qita/_cli_app.py` | Single-file HTML/server surface | Split only when qita behavior tests can preserve rendered output |
| `qitos/engine/engine.py` | Canonical lifecycle narrative | Keep loop readable; move only phase-specific internals |
| `qitos/engine/_model_runtime.py` and `action_executor.py` | Model and tool execution detail | Refactor by phase, not by generic service abstraction |
| `qitos/kit/tool/internal/coding_impl.py` | Canonical coding toolset | Keep user-facing tool names stable; extract only repeated helpers |
| `qitos/kit/tool/experimental/security_research/*` | Explicit opt-in security research tools | Never default-export from broad public surfaces |
| `qitos/models/openai.py` | Provider-native response and multimodal support | Keep provider logic out of Engine |
| `qitos/protocols.py` | Protocol-to-parser/prompt table | Keep as the single protocol registry |

## Boundary Findings

- `qitos` top-level exports are kernel-first and should stay limited to stable contracts such as `AgentModule`, `Engine`, `Decision`, `Action`, `ToolRegistry`, `Task`, and trace/run specs.
- `qitos.core` has no legitimate dependency on `qitos.engine`, `qitos.kit`, `qitos.benchmark`, `qitos.recipes`, examples, or zoo packages.
- `qitos.engine` owns runtime orchestration and may depend on `qitos.core` plus trace support, but should not hard-code benchmark or product-agent logic.
- `qitos.kit` is a curated implementation layer. Security research tooling must be imported from explicit security paths, not broad defaults.
- `qitos.workflow` is optional and must not make `qitos-dag` a core import requirement.
- Benchmark adapters may depend on recipes or core framework types. Recipes may use benchmark adapters when they are benchmark-specific recipes, but core and engine must remain benchmark-agnostic.

## Optional Dependency Findings

- `qitos.workflow.*` imports `qitos_dag`; this is acceptable only behind the optional workflow package surface.
- Provider and web modules use `requests`; it is currently a core runtime dependency.
- Benchmark adapters lazily import `datasets` and `huggingface_hub`, which is the correct pattern for benchmark extras.
- Embedding adapters lazily import `openai`, which is the correct pattern for model/provider extras.
- Playwright is imported lazily inside the web provider path.

## v0.8.0 Cleanup Decisions

- Keep the single `AgentModule + Engine` kernel as the public mental model.
- Keep `qitos.protocols` as the protocol-to-parser/prompt contract registry.
- Remove security audit builders from broad `qitos.kit.toolset` and old flat compatibility `__all__` lists; keep explicit module paths available.
- Make `qitos.workflow` a lazy optional facade so importing the package can give clear install guidance when `qitos-dag` is absent.
- Treat product-grade agents and high-risk security workflows as qitos-zoo candidates, not core package defaults.

## Guardrails To Keep

- Public-surface tests must fail if product agents or experimental security toolsets enter top-level `qitos`, `qitos.kit`, `qitos.kit.tool`, or broad `qitos.kit.toolset` exports.
- Architecture layout tests must fail if `qitos.core` starts importing implementation, benchmark, recipe, example, or zoo modules.
- Workflow tests should keep validating real workflow behavior when `qitos-dag` is installed, while import tests should keep the optional dependency boundary clear.
- Optional qitos-zoo and workflow integration suites are skipped by the default core gate unless `QITOS_RUN_OPTIONAL_INTEGRATION_TESTS=1` is set with matching external packages.
