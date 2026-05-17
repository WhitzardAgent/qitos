# QitOS Architecture

## High-Level Design

QitOS is organized around one core execution narrative:

`AgentModule -> Engine -> Decision -> ActionExecutor -> Env/Tools -> Trace/qita`

The project is intentionally kernel-first. Higher-level patterns, examples, and benchmark adapters are expected to build on the same runtime loop rather than bypass it.

## Core Components

### `qitos.core`

Defines the framework contracts:

- `AgentModule`
- `StateSchema`
- `Decision`
- `Action`
- `BaseTool` and tool specs
- task, memory, history, and error abstractions

### `qitos.engine`

Owns the runtime:

- state initialization and control flow
- decision normalization and parser integration
- action execution
- hook dispatch
- env capability checks
- trace and context telemetry

### `qitos.kit`

Provides curated building blocks on top of the core:

- parsers
- prompts
- planning helpers
- env implementations
- memory/history implementations
- canonical toolsets such as `CodingToolSet`

### `qitos.trace` and `qitos.qita`

Provide observability:

- structured run artifacts
- event and step records
- replay and run inspection
- browser-friendly trace views

### `qitos.benchmark`

Contains adapters and runtime support for benchmark-oriented workflows such as GAIA, Tau-Bench, and CyBench.

## Key Design Decisions

### Kernel First

The stable surface is the execution kernel, not every convenience wrapper in `kit`.

### Decision-Centric Runtime

The engine executes `Decision` objects, even when the model output originates as text or provider-specific response payloads.

### Canonical Tool Surface

`CodingToolSet` is the standard file/shell/codebase tool bundle. Deprecated compatibility wrappers were removed to keep one primary authoring path.

### Opt-In Experimental Security Research

Higher-risk security research tools live under `qitos.kit.tool.experimental.security_research` and are not part of the default public surface.

### Observability As A First-Class Feature

Trace artifacts and qita views are part of the framework contract, not an afterthought.

## Typical Flow

1. An `AgentModule` produces or delegates a `Decision`.
2. The `Engine` normalizes the decision and records observability.
3. `ActionExecutor` validates and executes actions through tools and env ops.
4. The agent reduces the observation into updated state.
5. Trace artifacts and qita views expose the full run.

## Repository Structure

- `qitos/`: framework source
- `tests/`: regression and behavior tests
- `docs/`: Mintlify documentation content
- `examples/`: canonical patterns and reference agents
- `templates/`: starter agent layouts

## Core vs Kit vs Recipes vs Examples vs Zoo

- Core: `qitos.core`, `qitos.engine`, `qitos.trace`, and `qitos.qita` define the stable kernel and observability contracts.
- Kit: `qitos.kit`, `qitos.models`, `qitos.protocols`, and `qitos.render` provide curated framework extensions when they are generic and reusable.
- Recipes: `qitos.recipes` contains reusable research baselines and benchmark methods, not full applications.
- Examples: `examples/` is a small teaching path with one concept per file.
- Zoo: `qitos-zoo` owns product-grade and showcase-grade applications such as `qitos-coder` and `qitos-cyber-agent`.

## What Must Not Enter Core

- Claude Code-style product agents
- PentAGI-style cybersecurity agents
- full SWE, desktop, EPUB, or SkillHub product workflows
- offensive or high-risk security tools in default imports or demos
- benchmark datasets, large generated artifacts, local absolute paths, or secrets
- dependencies needed only by product apps

## Promotion Rule

Code can move from zoo to QitOS only if it is generic, tested, documented, dependency-conscious, and needed by multiple independent apps. Product-specific code stays in qitos-zoo.

## Non-Goals

- hiding execution semantics behind opaque abstractions
- maintaining multiple long-term public APIs for the same tool workflow
- treating examples as production-only integrations detached from the kernel
