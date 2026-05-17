# Examples

`examples/` is canonical learning material, not a product showcase.

The learning path is:

```text
StateSchema -> prepare -> Engine/Model decide -> tool/env -> reduce -> trace/qita
```

## Directory Map

- `examples/quickstart/`: the smallest runnable coding agent
- `examples/patterns/`: one design axis per example
- `examples/real/`: minimal real agents only
- `examples/benchmarks/`: thin wrappers over `qitos.recipes` and `qitos.benchmark`

## Recommended First Run Order

```bash
export OPENAI_API_KEY="your_api_key"
qit demo minimal
python examples/quickstart/minimal_agent.py
python examples/patterns/react.py
python examples/patterns/planact.py
python examples/patterns/reflexion.py
python examples/patterns/tot.py
python examples/real/research_harness_agent.py
python examples/real/coding_agent.py
```

Benchmark wrappers:

```bash
python examples/benchmarks/gaia_eval.py --help
python examples/benchmarks/tau_bench_eval.py --help
python examples/benchmarks/cybench_eval.py --help
```

## Examples Policy

- One concept per file.
- No heavy hidden dependencies.
- No local absolute paths.
- No product clone as a canonical example.
- Benchmark wrappers call framework recipes/adapters and do not own canonical logic.
- Security-sensitive workflows are opt-in and not part of the quickstart.

## Full Applications

Full applications live in `qitos-zoo`, including:

- `qitos-coder`: a Claude Code-inspired coding agent built with QitOS.
- `qitos-cyber-agent`: a PentAGI-inspired cybersecurity agent built with QitOS.

Some product-like files remain temporarily in `examples/real/` with migration banners while the zoo repository is seeded from `plans/qitos_zoo_migration/`.
