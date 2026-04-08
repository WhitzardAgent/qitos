# Examples

`examples/` is organized as a small canonical learning path for QitOS.

The mental model we want users to internalize is:

```text
StateSchema -> prepare -> Engine/Model decide -> tool/env -> reduce -> trace/qita
```

## Directory map

- `examples/quickstart/`: the smallest runnable coding agent
- `examples/patterns/`: one design axis per example (`react`, `planact`, `reflexion`, `tot`)
- `examples/real/`: practical single-task agents that still follow the canonical authoring path
- `examples/benchmarks/`: operational runners for GAIA, Tau-Bench, and CyBench

## What stays consistent

All teaching-first examples follow the same shape:

- top-level constants for task / workspace / model defaults
- one explicit `StateSchema`
- one `AgentModule`
- direct model setup from environment variables or a family preset builder
- one `agent.run(...)`
- terminal UI and trace on by default

Pattern examples change one design axis at a time.
Real examples add capability on top of the same authoring path instead of inventing a different structure.

## Recommended first run order

```bash
export OPENAI_API_KEY="your_api_key"
qit demo minimal
python examples/quickstart/minimal_agent.py
python examples/patterns/react.py
python examples/patterns/planact.py
python examples/real/coding_agent.py
python examples/real/claude_code_agent.py
python examples/real/code_security_audit_agent.py
```

Then continue with:

```bash
python examples/patterns/reflexion.py
python examples/patterns/tot.py
python examples/real/swe_agent.py
python examples/real/computer_use_agent.py
python examples/real/epub_reader_agent.py
```

If you want the same ReAct shape but with built-in context compaction:

```bash
python examples/real/react_compact_agent.py
```

## Benchmark runners

Benchmark/eval runners live under `examples/benchmarks/`.
They are intentionally more operational and may keep benchmark loops, resume logic, JSONL export, and concurrency.

```bash
python examples/benchmarks/gaia_eval.py --help
python examples/benchmarks/tau_bench_eval.py --help
python examples/benchmarks/cybench_eval.py --help
```

## Required environment variables

The minimal demo and all model-backed examples below require an API key. Never commit API keys.

```bash
export OPENAI_BASE_URL="https://api.siliconflow.cn/v1/"
export OPENAI_API_KEY="your_api_key"
```

Optional:

```bash
export QITOS_MODEL_FAMILY="qwen"
export QITOS_MODEL="Qwen/Qwen3-8B"
```

For the v0.4 flagship example, the recommended path is now preset-first:

```bash
python examples/real/claude_code_agent.py \
  --model-family kimi \
  --model-name kimi-k2-0905-preview \
  --base-url https://api.moonshot.ai/v1
```

## Notes

- `examples/quickstart/minimal_agent.py` now wraps the packaged `qit demo minimal` flow, so the README quickstart and example path stay aligned around the same minimal coding agent.
- `examples/real/epub_reader_agent.py` expects a local EPUB at `./playground/epub_reader_agent/book.epub`.
- `examples/real/code_security_audit_agent.py` shows the new composition-first path: pass `toolset=[...]` and let QiTOS flatten `SecurityAuditToolSet + CodingToolSet + TaskToolSet` automatically.
- `examples/real/react_compact_agent.py` shows the smallest opt-in path for `CompactHistory`: keep the same agent shape and only swap the history preset.
- `examples/real/claude_code_agent.py` is the fuller Claude Code-style coding example and now doubles as the v0.4 multi-family preset showcase.
- `examples/real/skillhub_github_agent.py` is an advanced third-party skill example. Read it after the core canonical path.
- benchmark runners may require dataset download or local benchmark assets before full runs.
