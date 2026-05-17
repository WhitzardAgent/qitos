# Core Governance Audit

## Baseline Snapshot

Branch: `chore/core-governance-zoo-split`.

Top-level structure includes framework source (`qitos/`), tests (`tests/`), docs (`docs/`), canonical examples (`examples/`), plans (`plans/`), templates (`templates/`), assets (`assets/`), playground/runs, and local assistant metadata.

`qitos/*` packages currently visible:

- Core/stable: `core`, `engine`, `trace`, `qita`
- Framework extensions: `kit`, `models`, `render`, `protocols.py`, `prompting.py`, `harness`
- Research operations: `benchmark`, `recipes`, `evaluate`, `metric`, `debug`
- Runtime support: `cache`, `checkpoint`, `config`, `experiment`
- Product/app candidate: `examples`
- CLI/demo: `cli.py`, `demo`

`examples/*` currently visible:

- `examples/quickstart/minimal_agent.py`
- `examples/patterns/{react,planact,reflexion,tot,delegate,fanout,handoff}.py`
- `examples/benchmarks/{gaia_eval,tau_bench_eval,cybench_eval,cybergym_eval}.py`
- `examples/real/*` practical and product-like agents

Initial validation:

- `python -m compileall qitos examples tests`: passed.
- `pytest -q`: 15 failed, 623 passed, 20 skipped before this sprint's regression repairs.

Suspected boundary problems:

- `qitos.examples.pentagi` is packaged as a QitOS module even though it is a full PentAGI-style product app.
- `examples/real` recommends product-grade agents in the first-run path.
- `qitos.kit` default imports expose security audit app/tooling surfaces.
- Docs contain local absolute paths and product-clone positioning.
- `setup.py` excludes top-level `examples*` but not `qitos.examples*`.

## Internal Builtin Boundary Audit

| Package/file | Classification | Notes and action |
| --- | --- | --- |
| `qitos.__init__.py` | CORE_STABLE | Top-level public surface. Slim to core contracts and run specs; do not export product, cache/config/experiment conveniences by default. |
| `qitos.core` | CORE_STABLE | Agent, state, decision, action, tool, env, task, memory/history contracts. Keep. |
| `qitos.engine` | CORE_STABLE | Runtime loop, parser/model/action/control/env/handoff internals, hooks, recovery. Keep and repair regressions. |
| `qitos.trace` | CORE_STABLE | Trace artifacts and writer contracts. Keep. |
| `qitos.qita` | OBSERVABILITY | First-class trace inspection. Keep. |
| `qitos.demo` | DEMO | Keep only `minimal` as canonical first run. |
| `qitos.cli.py` | FRAMEWORK_EXTENSION | Keep `demo minimal`, `bench`, `experiment`, `skill`; do not add product app demos. |
| `qitos.kit` | FRAMEWORK_EXTENSION | Generic curated building blocks. Remove security-specific default exports from flat `qitos.kit`. |
| `qitos.kit.tool` | FRAMEWORK_EXTENSION | Atomic and preset tools. Security research must require explicit module paths. |
| `qitos.kit.tool.experimental.security_research` | EXPERIMENTAL | Explicit opt-in only; not imported by `qitos` or default demos. |
| `qitos.kit.toolset` | FRAMEWORK_EXTENSION | Scenario builders. Review security and product-like presets before stabilizing. |
| `qitos.kit.agent.security_audit_agent` | SHOULD_MOVE_TO_ZOO | Product-like defensive audit agent template. Keep only temporarily behind explicit imports; stage related apps for zoo. |
| `qitos.models` | FRAMEWORK_EXTENSION | Provider adapters and model contracts. Optional dependencies belong in extras. |
| `qitos.render` | OBSERVABILITY | Terminal/qita-adjacent rendering. Keep generic; rename product-styled hooks later if needed. |
| `qitos.harness` | FRAMEWORK_EXTENSION | Model family presets and policy adapters. Keep generic. |
| `qitos.recipes` | RECIPE | Keep benchmark/desktop baseline recipes only if reusable and thin. |
| `qitos.benchmark` | BENCHMARK_ADAPTER | Keep thin adapters/runners. Review `pentagi_e2e` and cyber benchmark surfaces for opt-in safety. |
| `qitos.evaluate`, `qitos.metric` | FRAMEWORK_EXTENSION | Generic evaluation contracts. Keep if dependency-light. |
| `qitos.debug` | FRAMEWORK_EXTENSION | Developer debugging/replay helpers. Keep if dependency-light. |
| `qitos.cache`, `qitos.checkpoint`, `qitos.config`, `qitos.experiment` | FRAMEWORK_EXTENSION | Useful runtime support but not top-level public API for this boundary pass. |
| `qitos.examples.pentagi` | SHOULD_MOVE_TO_ZOO | Full PentAGI-inspired cybersecurity product app. Exclude from packaging and stage under `qitos-cyber-agent`. |

## Concrete Actions Taken

- Added `CORE_BOUNDARY.md`.
- Staged zoo candidates under `plans/qitos_zoo_migration/`.
- Excluded `qitos.examples*` and `plans/qitos_zoo_migration` from packaging.
- Slimmed `qitos.__init__` to core/public contracts.
- Removed security-audit exports from default `qitos.kit` and `qitos.kit.tool` flat surfaces.
- Added migration banners to product-like `examples/real` files that remain temporarily.
- Repaired engine final/wait handling so finalization, parser feedback, hooks, checkpoints, and memory records follow the normal loop.

## Final Validation

- `python -m compileall qitos examples tests`: passed.
- `pytest -q`: 649 passed, 23 skipped.
- `qit --help`: passed after restoring `--help` exit code 0.
- `qit demo minimal --help`: passed.
- `qita --help`: passed.
- `python -m pip install -e .`: build-isolation path could not fetch `setuptools>=68` because network/DNS is restricted and escalation was rejected by the environment.
- `python -m pip install -e . --no-build-isolation`: built editable metadata/wheel locally, then failed to write into user site-packages with `Operation not permitted`.

## Files Moved Or Staged For qitos-zoo

- `plans/qitos_zoo_migration/apps/qitos-coder/`
- `plans/qitos_zoo_migration/apps/qitos-cyber-agent/`
- `plans/qitos_zoo_migration/apps/experimental/`

## Public API Changes

- `qitos.__init__` now focuses on kernel/public contracts.
- Cache/config/checkpoint/experiment convenience imports remain available from their package paths, but are no longer top-level defaults.
- `qitos.kit` and `qitos.kit.tool` no longer export security-audit surfaces from the broad default import list.

## Dependency Changes

- No new runtime dependencies were added.
- `qitos.examples*` and `plans*` are excluded from installable packages.

## Remaining Risks

- `qitos.benchmark.cybench`, `cybergym`, and `pentagi_e2e` need a follow-up safety/API review.
- Some docs still describe product-like tutorials; this pass redirects top-level docs and examples policy, but full Mintlify pruning may need a dedicated docs PR.
- Zoo-staged files preserve source layout and may need package/import adaptation in the future `qitos-zoo` repository.
- Editable install could not complete because this sandbox cannot write to the user site-packages path.

## Suggested Next PR

Remove or fully relocate the temporary product examples after the real `qitos-zoo` repository exists, then replace product tutorial pages with links into qitos-zoo docs.
