# CyberGym PoC Generation Agent

An autonomous agent that generates proof-of-concept (PoC) files for software vulnerabilities. Given a vulnerability description and a pre-patch codebase, the agent produces a raw input file that triggers the underlying bug when fed to the vulnerable binary.

Built on [QitOS](https://github.com/Qitor/qitos) -- a relaxable agentic framework for researchers.

## How It Works

The agent follows a four-phase state machine driven by QitOS's `PhaseEngine`:

```
INGESTION -> INVESTIGATION -> FORMULATION -> VERIFICATION
     ^              ^                           |
     |              |  (discriminant failure)   |
     |              +---------------------------+
     |              |  (exhausted attempts)     |
     +--------------+---------------------------+
```

1. **Ingestion** -- Read vulnerability description, parse `submit.sh` for harness info, discover corpus files, build repo index, classify bug type
2. **Investigation** -- Search and read source code, trace input-to-bug data flow, form a trigger hypothesis (max ~6 steps, forced at step 7)
3. **Formulation** -- Write a PoC file using the detected strategy (corpus mutation, Python binary generation, or text)
4. **Verification** -- Submit PoC via `submit_poc`, analyze exit codes, iterate on failure with discriminant-aware feedback

### Key Design Insights (from 100-run trace analysis)

Based on analysis of 73 successful and 27 failed runs on CyberGym tasks:

- **75% of successes need multiple submit iterations** (avg 6.9). The agent submits early and iterates based on server feedback.
- **77% of successes use corpus/sample files** as PoC starting points. The agent discovers and suggests corpus mutation.
- **Discriminant failures** (both vul and fix crash) trigger re-investigation to find the specific vulnerable code path.
- **Step-based forcing** at step 7 ensures the agent transitions from investigation to formulation before spending too many steps reading code.

## Quick Start

### Installation

```bash
# Install QitOS framework
pip install git+https://github.com/Qitor/qitos.git

# Install CyberGym task generation (for run_local mode)
pip install -e /path/to/cybergym

# Install this package
pip install -e /path/to/cybergym_agent
```

### Run on a CyberGym Task (Local Mode)

The local runner uses `HostEnv` (your local filesystem) instead of Docker. The agent reads source code, searches the codebase, and writes PoC files locally. No verification server is required.

```bash
python -m cybergym_agent.run_local \
    --task-id arvo:3938 \
    --data-dir /path/to/repos/data \
    --difficulty level1 \
    --model Qwen3.5-122B \
    --api-key 'YOUR_API_KEY' \
    --base-url https://your-api-endpoint/v1 \
    --max-steps 30
```

### Run with Mock Server (Testing)

For testing without a real CyberGym server:

```bash
# Start mock server in background
python -m cybergym_agent.mock_server --port 8666 &

# Run agent pointing to mock server
python -m cybergym_agent.run_local \
    --task-id arvo:3938 \
    --data-dir /path/to/repos/data \
    --server http://localhost:8666 \
    --api-key 'YOUR_API_KEY' \
    --base-url https://your-api-endpoint/v1
```

### Run with Docker (Full Verification)

For full PoC verification against the CyberGym server, use the CLI with Docker:

```bash
python -m cybergym_agent run /path/to/task_dir \
    --model qwen3-coder-next \
    --api-key sk-xxx \
    --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
    --server http://localhost:8000 \
    --max-steps 30
```

**Arguments:**

| Argument | Required | Description |
|---|---|---|
| `--task-id` | Yes* | CyberGym task ID (e.g., `arvo:3938`). Requires `--data-dir`. |
| `--data-dir` | Yes* | Path to CyberGym data root (containing `arvo/`, `oss-fuzz/`). |
| `--task-dir` | Yes* | Path to an already-prepared CyberGym task directory (alternative to `--task-id`). |
| `--difficulty` | No | Task difficulty: `level0` (repo only), `level1` (+ description), `level2` (+ error output), `level3` (+ patch diff). Default: `level1`. |
| `--model` | No | LLM model identifier. Default: `qwen3-coder-next`. |
| `--api-key` | Yes | API key for the LLM provider. |
| `--base-url` | Yes | Base URL for the LLM provider API. |
| `--server` | No | CyberGym verification server URL. Default: `http://localhost:8000`. |
| `--max-steps` | No | Maximum agent steps. Default: `30`. |

*`--task-id` and `--task-dir` are mutually exclusive; one is required.

### Run Tests

```bash
python -m cybergym_agent.test_agent
```

## Model Configuration

The agent uses QitOS's harness preset system (`build_model_for_preset`) to automatically configure the correct protocol and tool delivery for each model family. The Engine reads `llm.qitos_harness_metadata` to auto-detect the parser, protocol, and native tool calling preference -- no manual configuration needed.

| Model Family | Tool Delivery | Native Tool Calls | Protocol |
|---|---|---|---|
| Qwen | `api_parameter` | Preferred | `json_decision_v1` |
| OpenAI (GPT-4.x) | `api_parameter` | Not preferred | `json_decision_v1` |
| Anthropic (Claude) | `prompt_injection` | Not preferred | `react_text_v1` |
| Gemini | `prompt_injection` | Not preferred | `xml_decision_v1` |

The preset is selected automatically based on the model name.

## Context Management

The agent uses a four-level progressive compaction pipeline to stay within the model's context window, plus QitOS's built-in `ContextConfig` for overflow protection:

**Compaction (CyberGym-specific, in `context.py`):**

| Level | Name | LLM Call? | Description |
|---|---|---|---|
| 1 | Snip | No | Replace old tool results with clearance markers |
| 2 | MicroCompact | No | Compress long messages to preview + tail |
| 3 | Collapse | No | Proactive restructuring at 90% utilization |
| 4 | AutoCompact | Yes | LLM-based summarization of older rounds |

After compaction, a `PostCompactRestorer` re-injects critical context (vulnerability description, current PoC, last error trace, harness info, best PoC) so the agent doesn't lose key information.

**Overflow protection (QitOS built-in, in `ContextConfig`):**

| Setting | Value | Description |
|---|---|---|
| `tool_result_max_chars` | 4000 | Max chars per tool result |
| `conversation_max_rounds` | 10 | Max multi-turn tool rounds |
| `loop_max_repeats` | 3 | Max identical tool calls before loop detection |

## PoC Generation Strategies

The agent auto-detects the best PoC strategy based on bug type and available resources:

| Strategy | When | How |
|---|---|---|
| `corpus_mutate` | Corpus/sample files available | Copy and modify existing input files |
| `binary_python` | Binary format bugs (images, archives, etc.) | Use `python3 -c` with `struct.pack` to craft binary PoC |
| `hex` | Hex-encoded data needed | Use `xxd -r` or `printf` for binary content |
| `text` | Text-based bugs (injection, format string) | Use `write_file` directly |

## Verification Feedback

When a PoC is submitted, the server returns `vul_exit_code` and `fix_exit_code`. The agent interprets these as:

| vul_exit | fix_exit | Meaning | Agent Action |
|---|---|---|---|
| != 0 | 0 or None | **SUCCESS** | Stop |
| != 0 | != 0 | **Discriminant failure** | Re-investigate: make PoC more targeted |
| 0 | any | **Miss** | Re-formulate: PoC doesn't trigger the bug |

The agent also parses sanitizer output from `vul_stderr` to extract crash type (heap-buffer-overflow, use-after-free, etc.) and crash location, providing this feedback in the next iteration.

## Project Structure

```
cybergym_agent/
  __init__.py          # Public API exports
  __main__.py          # python -m cybergym_agent support
  agent.py             # CyberGymAgent -- main agent module (PhaseEngine, MemdirMemory)
  state.py             # CyberGymState -- typed state schema with PoC quality tracking
  memory.py            # CyberGymMemory -- legacy memdir memory (kept for compat)
  context.py           # Four-level progressive compaction pipeline
  submit_tool.py       # SubmitPoCTool -- PoC submission to CyberGym server
  env.py               # CyberGymEnv -- Docker environment for PoC execution
  adapter.py           # CyberGymAdapter -- CyberGym task -> QitOS Task conversion
  stop_criteria.py     # PoCVerificationCriteria -- custom stop condition
  mock_server.py       # Mock CyberGym server for testing
  test_agent.py        # Unit and integration tests
  cli.py               # Command-line interface (with Docker)
  run_local.py         # Local runner (code audit + PoC gen, no Docker)
```

## Bug Types

The agent automatically classifies bug types from the vulnerability description and provides type-specific guidance including PoC strategy:

- Buffer overflow (stack/heap) -- oversized input
- Use-after-free / double free -- allocate-free-use sequence
- Integer overflow / underflow -- values near INT_MAX/UINT_MAX
- Null pointer dereference -- empty/minimal input
- Format string -- format specifiers as input
- Race condition -- concurrent access triggers
- Command injection
- XSS
- SQL injection

## Dependencies

- `qitos` -- Agent framework ([GitHub](https://github.com/Qitor/qitos))
- `httpx` -- HTTP client for CyberGym server submission
- `pyyaml` -- YAML frontmatter parsing for memory files
- `openai` -- OpenAI-compatible API client
- `docker` -- Container management (optional, for full verification mode)
