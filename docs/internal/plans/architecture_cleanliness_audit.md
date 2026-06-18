# Architecture Cleanliness Audit

## Purpose

This plan defines a repository-wide audit and optimization program for improving QitOS codebase cleanliness, architectural boundaries, and long-term maintainability without creating a parallel architecture track.

The target outcome is not a cosmetic cleanup. The target outcome is a cleaner single-kernel framework where contributors can easily see:

- what belongs in stable framework contracts,
- what belongs in replaceable kit implementations,
- how runtime behavior flows through `AgentModule + Engine`,
- which surfaces are public and stable,
- which surfaces are experimental, recipe-level, benchmark-level, or qitos-zoo candidates.

## Guiding Constraints

- Preserve the canonical execution story: `AgentModule -> Engine -> Decision -> ActionExecutor -> Env/Tools -> Trace/qita`.
- Do not introduce `V1`, `V2`, `Legacy`, `Next`, or alias-based duplicate core concepts.
- Keep stable contracts in `qitos.core`; keep replaceable implementations in `qitos.kit`.
- Do not degrade trace clarity, qita replay/export usefulness, stop reasons, or hook payloads.
- Prefer narrow, reviewable PRs over one large rewrite.
- Do not move product-grade agents or high-risk security tooling closer to the default public API.
- Treat docs, README news, changelog, examples, and tests as part of every meaningful implementation phase.

## Current Baseline

Observed repository shape:

- Stable kernel: `qitos.core`, `qitos.engine`, `qitos.trace`, `qitos.qita`.
- Framework extensions: `qitos.kit`, `qitos.models`, `qitos.protocols`, `qitos.prompting`, `qitos.render`, `qitos.harness`.
- Research and reproducibility: `qitos.recipes`, `qitos.benchmark`, `qitos.evaluate`, `qitos.metric`, `qitos.experiment`.
- Runtime support: `qitos.cache`, `qitos.checkpoint`, `qitos.config`, `qitos.debug`, `qitos.mcp`.
- Workflow integration: `qitos.workflow`, backed by optional `qitos-dag`.
- Product boundary: `qitos_zoo` exists as the destination for product-like agents.
- Tests include freeze guards for public API, package layout, core loop behavior, qita, engine, checkpoint, tracing, benchmarks, and kit modules.

Primary code smells to audit:

- Core files that have grown too large or mix orchestration with feature-specific concerns.
- Concrete implementations leaking into core contracts.
- Product-like workflows remaining in framework packages, examples, or default exports.
- Benchmark-specific logic leaking into generic recipes or engine paths.
- Optional dependency imports at module import time instead of lazy integration points.
- Duplicate tool naming conventions and compatibility aliases obscuring the preferred authoring path.
- Docs or examples describing non-canonical patterns.
- Tests asserting historical behavior that conflicts with current architecture goals.

## Workstream 1: Measurement And Inventory

Goal: create a factual map before changing behavior.

Tasks:

- Generate a module inventory by package, file count, line count, import fan-in/fan-out, and public exports.
- Classify every top-level package into one of: `CORE_STABLE`, `ENGINE_RUNTIME`, `OBSERVABILITY`, `KIT_IMPLEMENTATION`, `MODEL_PROTOCOL`, `RECIPE`, `BENCHMARK_ADAPTER`, `WORKFLOW_OPTIONAL`, `PRODUCT_ZOO_CANDIDATE`, `EXPERIMENTAL`, or `INTERNAL_TOOLING`.
- Identify files above maintainability thresholds:
  - more than 800 lines,
  - more than 12 top-level classes/functions,
  - mixed ownership across package boundaries,
  - direct optional dependency import at module import time.
- Produce an import-boundary report:
  - `qitos.core` must not depend on `qitos.engine`, `qitos.kit`, `qitos.benchmark`, `qitos.recipes`, or product modules.
  - `qitos.engine` may depend on `qitos.core`, `qitos.trace`, and internal engine helpers, but should not hard-code benchmark/product behavior.
  - `qitos.kit` may depend on core contracts and optional implementation packages, but should keep optional imports lazy.
  - `qitos.benchmark` may depend on recipes and framework APIs, not the other way around.
- Record current public API exports from `qitos`, `qitos.core`, `qitos.engine`, `qitos.kit`, and `qitos.kit.toolset`.

Deliverables:

- `docs/internal/plans/architecture_inventory.md`
- Optional small script under `scripts/` if repeated inventory is useful.
- No behavior changes in this phase.

Validation:

```bash
pytest -q tests/test_public_surface.py tests/test_architecture_layout.py tests/test_p0_freeze_guards.py
```

## Workstream 2: Public Surface And Package Boundary Cleanup

Goal: make import paths communicate stability and ownership.

Tasks:

- Reconfirm top-level `qitos.__all__` contains stable kernel contracts only.
- Audit `qitos.core.__all__` for concrete implementation leakage.
- Audit `qitos.engine.__init__` so internal strategy classes remain internal unless intentionally stable.
- Audit `qitos.kit.__init__`, `qitos.kit.tool.__init__`, and `qitos.kit.toolset.__init__` for overly broad default exports.
- Move or mark product-grade app surfaces as qitos-zoo candidates.
- Ensure experimental security research tooling is opt-in by explicit module path only.
- Add or update freeze tests whenever a public boundary is clarified.

Acceptance criteria:

- Importing `qitos` has no optional provider, benchmark, workflow, desktop, or experimental security side effects.
- Product-like agent classes are not reachable from broad default imports.
- New tests fail if future changes accidentally widen the public surface.

Validation:

```bash
pytest -q tests/test_public_surface.py tests/test_architecture_layout.py tests/test_examples_policy.py
```

## Workstream 3: Engine Internal Cleanliness

Goal: keep the Engine as the single loop while reducing local complexity inside the largest runtime files.

Tasks:

- Audit `qitos/engine/engine.py` for responsibilities that already have private runtime helpers:
  - model decision,
  - action execution,
  - control flow,
  - environment lifecycle,
  - handoff,
  - trace and hook dispatch,
  - context budget,
  - checkpoint persistence.
- Move logic only when there is already a clear helper boundary or a repeated pattern.
- Keep `Engine.run()` readable as the authoritative lifecycle narrative.
- Avoid extracting abstractions that hide phase order or make trace emission harder to audit.
- Normalize duplicated final/wait/handoff/recovery branches only if tests can preserve existing behavior.
- Ensure `StepRecord`, `RuntimeEvent`, stop reasons, and hook payloads stay stable.

Acceptance criteria:

- `Engine.run()` still reads as the canonical loop.
- Private helpers have names tied to runtime phases, not vague service abstractions.
- No trace, qita, checkpoint, or hook regression.

Validation:

```bash
pytest -q tests/test_engine_core_flow.py tests/engine tests/e2e/test_hooks_lifecycle.py tests/test_engine_hooks.py
pytest -q tests/test_qita_cli.py tests/tracing tests/checkpoint
```

## Workstream 4: Tooling, Env, And Permission Hygiene

Goal: make tools composable, permission-aware, and environment-backed.

Tasks:

- Audit every class-based tool for `execute(args, runtime_context)` support.
- Keep `run(...)` compatibility, but avoid using it as the preferred new contract.
- Verify tools requiring filesystem/process/network access declare `ToolPermission` and/or `required_ops`.
- Ensure env-backed tools use `runtime_context["ops"]` where available.
- Remove duplicate naming paths only after compatibility tests exist.
- Review `ToolRegistry` aliases and namespaces for clear canonical names.
- Review permission pipeline and read-before-write integration for toolsets that mutate files.

Acceptance criteria:

- New tools can be reasoned about by schema, permission metadata, and required ops.
- Host filesystem/process assumptions are isolated in env implementations or explicit tools.
- Tool failure payloads remain structured enough for the Engine and qita.

Validation:

```bash
pytest -q tests/test_tool_registry_and_toolset.py tests/core/test_tool_schema.py tests/core/test_function_tool.py
pytest -q tests/test_permission_pipeline.py tests/test_tool_permission_spec.py tests/test_env_contract.py
pytest -q tests/kit/tool tests/engine/test_function_tool_engine_integration.py
```

## Workstream 5: Protocol, Parser, Prompt, And Model Boundaries

Goal: keep model-family adaptation out of agent and engine-specific hacks.

Tasks:

- Audit `qitos.protocols` as the single protocol-to-parser/prompt contract table.
- Ensure parser fallback and diagnostics remain trace-visible.
- Check that model adapters preserve provider-native tool call information only through normalized `ModelResponse` or Engine-supported paths.
- Keep prompt authoring through `PromptSpec`, `PromptBuilder`, and protocol renderers where possible.
- Avoid adding model-family special cases inside agents unless they are recipe-specific.

Acceptance criteria:

- Protocol changes are localized to protocol/harness/parser layers.
- Engine behavior remains protocol-agnostic after resolution.
- Parser diagnostics are visible in `StepRecord` and trace summaries.

Validation:

```bash
pytest -q tests/test_model_protocols.py tests/test_model_providers.py tests/test_domestic_model_harness.py tests/test_harness_presets.py
pytest -q tests/test_yaml_config.py tests/test_multimodal_capability_fallback.py
```

## Workstream 6: Recipes, Benchmarks, Examples, And qitos-zoo Boundary

Goal: make the learning path small and the research path reproducible.

Tasks:

- Audit `examples/` so each file teaches one canonical pattern and avoids product-app scope.
- Move or stage product-grade agents under `qitos_zoo` or migration plans.
- Ensure benchmark adapters convert external inputs into canonical `Task`.
- Keep benchmark-specific scoring/runtime code out of core and engine.
- Prefer recipes for reusable baseline methods; keep examples as thin runnable wrappers.
- Verify docs and Chinese docs point to the same canonical concepts.

Acceptance criteria:

- Examples are short, runnable, and documentation-like.
- Benchmark logic is thin and dataset-specific.
- Recipes can be called from docs, examples, and benchmark runners without duplication.

Validation:

```bash
pytest -q tests/test_examples_policy.py tests/test_examples_smoke.py tests/test_benchmark_gaia.py tests/test_benchmark_tau_bench.py
pytest -q tests/test_benchmark_cybench.py tests/test_benchmark_cybergym_recipe.py tests/test_zoo_eval_configs.py
```

## Workstream 7: Observability, Trace, qita, And Replay

Goal: protect the research value of every run while cleaning internals.

Tasks:

- Audit trace schema stability and event payload usefulness.
- Keep `run_id`, `step_id`, `phase`, `agent_id`, stop reason, parser diagnostics, and context telemetry visible.
- Verify qita board/replay/export can tolerate old and new trace records.
- Ensure handoff, delegate, fanout, checkpoint, and critic events remain distinguishable.
- Review trace payload sanitization so artifacts are inspectable but not brittle.

Acceptance criteria:

- Every runtime phase remains inspectable in qita.
- Trace summaries include enough reproducibility metadata for benchmark comparison.
- qita does not depend on private in-memory Engine state.

Validation:

```bash
pytest -q tests/test_qita_cli.py tests/tracing tests/test_engine_result_traces.py tests/test_wandb_trace_processor.py tests/test_mlflow_trace_processor.py
```

## Workstream 8: Dependency And Optional Import Hygiene

Goal: keep core install lightweight and optional integrations truly optional.

Tasks:

- Re-run dependency classification from `docs/internal/plans/dependency_audit.md`.
- Find imports of optional packages at module top level.
- Move provider SDKs, workflow, web/browser, desktop, benchmark, and tracing integrations behind extras or lazy imports.
- Ensure `pip install qitos` supports the minimal core path.
- Ensure extras remain documented in README and installation docs.

Acceptance criteria:

- Core import path works without model SDKs, qitos-dag, Playwright, W&B, MLflow, datasets, or Hugging Face.
- Optional feature failures produce clear install guidance.

Validation:

```bash
pytest -q tests/test_config_security.py tests/test_no_local_paths.py tests/test_hf_hub.py tests/test_workflow_integration.py
python -m build
python -m twine check dist/*
```

## Workstream 9: Documentation Synchronization

Goal: make docs reflect the cleaned architecture instead of historical drift.

Tasks:

- Update concept docs for any boundary or API cleanup.
- Keep `README.md` news concise and momentum-oriented.
- Update `CHANGELOG.md` under `Unreleased`.
- Keep Chinese docs reasonably aligned when public workflows change.
- Remove docs that encourage deprecated imports, product-app examples, or non-canonical tool contracts.
- Add diagrams only where they clarify cross-component flow.

Acceptance criteria:

- New users see `AgentModule + Engine` first.
- Contributors can infer package placement from docs.
- README, docs, examples, and tests agree.

Validation:

```bash
pytest -q tests/test_tutorial_snippets.py
```

## Execution Order

Recommended sequence:

1. Measurement and inventory.
2. Public surface freeze and package boundary cleanup.
3. Tool/env/permission contract cleanup.
4. Engine internal cleanup in small phase-preserving PRs.
5. Protocol/parser/model boundary cleanup.
6. Recipes/benchmarks/examples/qitos-zoo cleanup.
7. Observability and qita hardening.
8. Dependency hygiene and packaging verification.
9. Documentation synchronization and final release notes.

This order intentionally puts measurement and public boundaries before refactoring. It reduces the chance that internal cleanup accidentally changes what users import or what traces contain.

## PR Slicing

Prefer these PR shapes:

- PR 1: inventory report plus new or updated freeze tests, no behavior changes.
- PR 2: public import boundary cleanup.
- PR 3: tool/env permission metadata cleanup.
- PR 4-N: engine helper cleanup by phase, one runtime concern per PR.
- PR N+1: protocol/parser/harness cleanup.
- PR N+2: examples/recipes/benchmark boundary cleanup.
- PR N+3: docs/changelog/readme synchronization and final validation.

Avoid PRs that combine engine control-flow edits with public API moves or benchmark rewrites.

## Risk Register

| Risk | Mitigation |
| --- | --- |
| Engine refactor changes stop behavior | Add focused tests around final, wait, parser-error wait, critic retry, budget, env terminal, and cancellation. |
| Trace schema drift breaks qita | Run qita tests and compare sample `manifest/events/steps` artifacts before and after. |
| Public API cleanup breaks users | Freeze exports with tests and document migration paths. |
| Optional imports become hard dependencies | Add import-only tests in a minimal environment where possible. |
| Benchmark cleanup changes scoring | Keep benchmark PRs thin and validate sample tasks independently. |
| Docs drift from code | Update docs in the same PR and run tutorial snippet tests. |

## Definition Of Done

The cleanup program is complete when:

- package boundaries are documented and enforced by tests,
- top-level public imports are core-first and product-free,
- Engine internals are easier to navigate without changing the single loop model,
- tools consistently expose schema, permission, required ops, and structured results,
- optional integrations are lazy and extra-gated,
- examples and docs teach canonical patterns,
- trace/qita output remains at least as informative as before,
- `pytest -q` passes,
- stable-surface static checks pass:

```bash
flake8 qitos/core qitos/engine qitos/models qitos/trace
mypy qitos/core qitos/engine qitos/models qitos/trace
```

- packaging checks pass when release-facing files changed:

```bash
python -m build
python -m twine check dist/*
```

