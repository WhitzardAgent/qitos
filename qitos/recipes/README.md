# QitOS Recipes

Recipes are self-contained benchmark execution functions built on top of the QitOS kernel. Each recipe encapsulates the full lifecycle for one benchmark -- dataset loading, agent construction, model wiring, execution, scoring, and result serialization -- so that running a benchmark is a single function call.

## Recipes vs `qitos/benchmark/`

`qitos/benchmark/` is the **adapter layer**: it converts external datasets (GAIA, TauBench, CyBench, CyberGym) into QitOS `Task` objects and handles dataset I/O. Adapters do not run agents or produce scores.

`qitos/recipes/` is the **execution layer**: it uses those adapters, constructs an `AgentModule`, wires a model via the harness, runs the agent loop, evaluates results, and writes `BenchmarkRunResult` rows. Every recipe produces reproducible, machine-readable output.

## Available Recipes

### Benchmark Recipes (`qitos.recipes.benchmarks`)

| Recipe | Module | Agent | Description |
|---|---|---|---|
| GAIA | `recipes.benchmarks.gaia` | `OpenDeepResearchGaiaAgent` | Web-search research agent for the GAIA benchmark. Uses WebSearch, VisitURL, CodingToolSet with ReActTextParser. |
| TauBench | `recipes.benchmarks.tau_bench` | `TauBenchAgent` | Policy-compliant tool-use agent for TauBench (retail/airline). Wraps the Tau runtime env and evaluates with reward-based metrics. |
| CyBench | `recipes.benchmarks.cybench` | `CyBenchReactAgent` | CTF-style cybersecurity agent for CyBench. Supports guided subtask and unguided modes, Docker or host execution, and partial-match scoring. |
| CyberGym | `recipes.benchmarks.cybergym` | Delegates to `qitos.benchmark.cybergym` | Thin wrapper around the CyberGym verification-server flow. Accepts task ID, server URL, and difficulty level. |

### Desktop Recipes (`qitos.recipes.desktop`)

| Recipe | Module | Agent | Description |
|---|---|---|---|---|
| OSWorld Starter | `recipes.desktop.osworld_starter` | `OpenAICUAAgent` | Computer-use baseline agent with screenshot/a11y observation, grounding critic, and desktop action protocol. |

## Usage

### Programmatic

Each benchmark recipe exposes a `main()` entry point and composable building blocks:

```python
from qitos.recipes.benchmarks.gaia import (
    execute_gaia_task,
    build_gaia_benchmark_result,
    main as run_gaia,
)
from qitos.recipes.benchmarks.cybench import (
    execute_cybench_task,
    build_cybench_benchmark_result,
    main as run_cybench,
)
from qitos.recipes.desktop.osworld_starter import (
    execute_desktop_task,
    build_benchmark_result,
    main as run_desktop,
)

# Run a single GAIA task
execution = execute_gaia_task(
    adapter=adapter, record=record, split="validation",
    idx=0, root=workspace_root, args=args,
    run_spec=run_spec, experiment_spec=experiment_spec,
)
result = build_gaia_benchmark_result(execution)

# Run a single CyBench task (guided mode)
execution = execute_cybench_task(
    args=args, adapter=adapter, idx=0, record=record,
    root=workspace_root, trial=0,
    run_spec=run_spec, experiment_spec=experiment_spec,
)
result = build_cybench_benchmark_result(execution)

# Run a desktop task
execution = execute_desktop_task(task=task, smoke=True)
result = build_benchmark_result(execution)
```

### CLI

Every recipe can also be invoked as a script:

```bash
python -m qitos.recipes.benchmarks.gaia --single-index 0 --max-steps 16
python -m qitos.recipes.benchmarks.tau_bench --tau-env retail --single-index 0
python -m qitos.recipes.benchmarks.cybench --single-index 0 --cybench-root references/cybench
python -m qitos.recipes.benchmarks.cybergym --task-id TASK_001 --server http://localhost:8080
python -m qitos.recipes.desktop.osworld_starter --smoke
```

## Shared Helpers (`_shared.py`)

Benchmark recipes share a common execution pipeline in `recipes.benchmarks._shared`:

- **`build_example_specs()`** -- Construct `RunSpec` and `ExperimentSpec` from benchmark name, split, model, and trace configuration.
- **`default_output_path()`** -- Generate a timestamped JSONL output path.
- **`execute_example_jobs()`** -- Run a list of job dicts through a recipe runner with optional concurrency and resume support. Writes results to JSONL.
- **`print_benchmark_summary()`** / **`print_single_result()`** -- Print structured JSON summaries to stdout.

Every benchmark recipe's `main()` uses this pipeline: load records, build work items, pass them to `execute_example_jobs()` with a recipe-specific runner, then print the summary.

## eval_config.yaml

Agents in `qitos_zoo/` declare their benchmark capabilities through `eval_config.yaml` files. These configs specify:

- **agent** -- name, factory function, required tools, and environment type
- **benchmarks** -- which benchmark suites the agent supports, with dataset, split, max_steps, eval_metric, categories, and timeout
- **serialization** -- output format (e.g. `runstate_json`) and schema version
- **defaults** -- fallback model, max_steps, and temperature

Example (`qitos_zoo/qitos_cyber/eval_config.yaml`):

```yaml
agent:
  name: qitos_cyber
  factory: qitos_zoo.qitos_cyber.snowl_compat.create_snowl_agent
  required_tools:
    - shell
    - search_network
  required_env:
    type: host
    capabilities:
      - filesystem
      - command
      - network

benchmarks:
  cybench:
    dataset: cybench/cybench-v1
    split: test
    max_steps: 200
    eval_metric: completion_rate
  cybergym:
    dataset: cybergym/cybergym-v1
    split: test
    max_steps: 150
    eval_metric: task_completion

defaults:
  model: null
  max_steps: 200
  temperature: 0.0
```

## Relationship to FamilyPreset

Recipes that use the harness layer (desktop, and any recipe calling `build_model_for_preset`) resolve model configuration through `FamilyPreset` objects. A `FamilyPreset` captures the recommended protocol, tool delivery mode, context window, and default hyperparameters for a model family:

```python
from qitos.harness import resolve_family_preset, build_harness_policy, build_model_for_preset

# Resolve defaults for Qwen models
preset = resolve_family_preset("qwen")
# preset.recommended_max_steps, preset.recommended_temperature, etc.

# Build a fully-wired harness policy
harness = build_harness_policy(model_name="Qwen/Qwen3-8B", family_id="qwen")

# Or build a model directly with all preset defaults applied
llm = build_model_for_preset(
    model_name="Qwen/Qwen3-8B",
    family_id="qwen",
    api_key="...",
    temperature=preset.recommended_temperature,
    max_tokens=2048,
)
```

The desktop recipe uses this flow via `configure_runtime_for_task()`, which resolves the preset, builds the harness, and returns a runtime dict the agent consumes. Benchmark recipes in `recipes/benchmarks/` construct `OpenAICompatibleModel` directly but can be adapted to use `build_model_for_preset` for preset-aware model selection.
