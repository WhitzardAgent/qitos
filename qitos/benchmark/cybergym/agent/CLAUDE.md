# CyberGym PoC Generation Agent

## Overview

The CyberGym PoC Generation Agent bridges the gap between natural-language vulnerability reports and executable Proof-of-Concept (PoC) scripts. Given a vulnerability description (text) and a pre-patch codebase (source archive), the agent produces a raw input file that, when fed to the vulnerable binary, triggers the underlying bug.

This is CyberGym Level 1 difficulty: the agent receives `repo-vul.tar.gz` and `description.txt` but no error output, patch diff, or reference PoC.

## Architecture

The agent synthesizes three foundations:

- **Raschka's theoretical framework** for coding-agent architecture (Memory, Planning, Tool Use, Action Execution)
- **QitOS** as the runtime harness providing the Engine loop, typed state, tool registry, environment abstraction, and planning primitives
- **Claude-Code patterns** for tool design, the memdir long-term memory system, and the four-level progressive context management system

## Package Structure

```
cybergym_agent/
  __init__.py          # Public API exports
  __main__.py          # python -m cybergym_agent support
  agent.py             # CyberGymAgent -- main agent module
  state.py             # CyberGymState -- typed state schema
  memory.py            # CyberGymMemory -- memdir long-term memory
  context.py           # Four-level progressive compaction pipeline
  submit_tool.py       # SubmitPoCTool -- PoC submission to CyberGym server
  env.py               # CyberGymEnv -- Docker environment for PoC execution
  adapter.py           # CyberGymAdapter -- CyberGym task -> QitOS Task conversion
  stop_criteria.py     # PoCVerificationCriteria -- custom stop condition
  cli.py               # Command-line interface (with Docker)
  run_local.py         # Local runner (code audit + PoC gen, no Docker)
  agent.md             # This file
```

## Component Details

### CyberGymState (`state.py`)

Extends `qitos.core.state.StateSchema` with domain-specific fields:

| Field Group | Fields | Purpose |
|---|---|---|
| Vulnerability context | `vulnerability_description`, `cve_id`, `bug_type`, `affected_component` | Stable across steps; populated during ingestion |
| CyberGym metadata | `task_id`, `agent_id`, `checksum`, `server_url` | Required for PoC submission |
| Investigation findings | `vulnerable_files`, `vulnerable_functions`, `input_entry_points`, `trigger_hypothesis`, `repo_index` | Accumulated during investigation phase |
| Planning | `plan`, `plan_cursor` | Numbered plan with cursor tracking |
| PoC iteration | `poc_path`, `poc_attempts`, `last_error_trace`, `last_verification_result` | Tracks PoC writing and testing |
| Phase tracking | `current_phase` | One of: ingestion, investigation, formulation, verification |
| Workspace | `workspace_root`, `repo_dir` | Filesystem paths |

Key method: `is_verified()` checks if `vul_exit_code != 0` and `fix_exit_code == 0` (or they differ), indicating differential behavior between vulnerable and patched binaries.

### CyberGymMemory (`memory.py`)

Implements the memdir protocol over QitOS's `Memory` ABC. Four memory types stored as Markdown files with YAML frontmatter:

| Type | Directory | Content |
|---|---|---|
| `user` | `~/.cybergym/memory/user/` | Global preferences (cross-project) |
| `feedback` | `<workspace>/.cybergym/memory/feedback/` | Verified behavioral rules (e.g., PoC strategies per bug type) |
| `project` | `<workspace>/.cybergym/memory/project/` | Task-specific knowledge |
| `reference` | `<workspace>/.cybergym/memory/reference/` | External resource pointers (server URLs, CVE databases) |

Index file `MEMORY.md` is auto-loaded into context at task start. Constraints: max 200 lines, 25KB total, 150 chars per entry.

After successful PoC generation, the agent saves a `feedback`-type memory recording the effective strategy for that bug type.

### Context Management (`context.py`)

Four-level progressive compaction pipeline built on top of QitOS's `CompactHistory`:

| Level | Name | LLM Call? | Class | Description |
|---|---|---|---|---|
| 1 | Snip | No | `SnipCompactor` | Replace old tool results with `[Old tool result content cleared]` markers. Keeps last 4 compressible messages. |
| 2 | MicroCompact | No | `MicroCompactor` (pre-built in QitOS) | Compress long messages to preview + tail. Configured with lower thresholds for shell output. |
| 3 | Collapse | No | `CollapseGate` | Proactive restructuring at 90% context utilization. Force-snips + force-microcompacts + triggers summary if still over budget. |
| 4 | AutoCompact | Yes | `SummaryCompactor` (pre-built in QitOS) | LLM-based summarization of older rounds with CyberGym-specific summary prompt. |

Additional components:
- **CompactionCircuitBreaker**: Stops compaction after 3 consecutive failures to prevent compress-expand-recompress cycles.
- **PostCompactRestorer**: After AutoCompact, restores vulnerability description, current PoC draft, and last error trace as system-role messages (50K token budget).
- **CyberGymContextHistory**: Extends `CompactHistory`, orchestrating the full pipeline in its `retrieve()` method.

CompactConfig tuning for PoC generation:
- `compact_long_messages_over_chars=600` (lower than default 900; shell outputs are verbose)
- `microcompact_preview_chars=180` (shorter preview; only need first/last few lines)
- `summary_max_chars=2000` (longer summaries; vulnerability findings are dense)
- `keep_last_rounds=3`, `keep_last_messages=10`
- `warning_ratio=0.75` (earlier warning than default 80%)

### SubmitPoCTool (`submit_tool.py`)

Custom `BaseTool` that POSTs PoC files to the CyberGym verification server. Parameters:
- `poc_path` (required): Path to the PoC file
- `task_id` (required): CyberGym task ID (e.g., `arvo:10400`)
- `agent_id` (required): Agent ID for this run
- `checksum` (required): SHA-256 checksum for verification
- `require_flag` (optional): CTF flag mode

Returns `{vul_exit_code, fix_exit_code, poc_id}` from the server. Uses httpx with 120s timeout.

### CyberGymEnv (`env.py`)

Extends `DockerEnv` with CyberGym-specific setup:
- Installs build-essential, python3, git, curl in the container
- Auto-extracts `repo-vul.tar.gz` into `/workspace/repo-vul`
- Provides `run_poc(poc_path, binary_path, timeout)` method for executing PoCs against the vulnerable binary
- Auto-detects vulnerable binary location

### CyberGymAdapter (`adapter.py`)

Converts CyberGym task directories to QitOS `Task` objects:
- Reads `description.txt`, `submit.sh`, `README.md` from the task directory
- Extracts `task_id` from `submit.sh` (regex matching `"arvo:NNNNN"` pattern)
- Computes SHA-256 checksum: `sha256(task_id + agent_id + salt)` with default salt `"CyberGym"`
- Generates UUID-based `agent_id`
- Builds `Task` with objective, inputs, resources, budget, and success criteria

### CyberGymAgent (`agent.py`)

Main agent class extending `AgentModule[CyberGymState, Dict, Any]`.

**Tool Registration (49 tools):**
1. `CodingToolSet` (43 tools): run_command, read_file, write_file, file_edit, glob, grep, search, list_files, task_create/get/update/list, web_fetch, etc.
2. `TaskToolSet` (5 tools): task_create, task_list, task_get, task_update, task_append_note
3. `SecurityAuditToolSet` (8+ tools): audit_config_scan, audit_hotspots, audit_secret_scan, etc.
4. `SubmitPoCTool` (1 tool): submit_poc

**Four-Phase State Machine:**

```
INGESTION -> INVESTIGATION -> FORMULATION -> VERIFICATION
     ^                                         |
     |          (if stuck after 3 attempts)    |
     +-----------------------------------------+
```

1. **Ingestion**: Read description, build repo index, load long-term memory, classify bug type
2. **Investigation**: Search codebase, trace input -> bug path, form trigger hypothesis
3. **Formulation**: Write PoC (binary via Python, or structured text)
4. **Verification**: Execute PoC, analyze output, submit to server, iterate on failure

**Bug Type Classification** (static method):
Detects: buffer_overflow, use_after_free, integer_overflow, null_pointer_dereference, format_string, race_condition, command_injection, xss, sql_injection. Provides bug-type-specific guidance in the system prompt.

**System Prompt Structure:**
- Stable prefix: persona + vulnerability description + memory index + tool schema
- Variable suffix: current phase + plan + investigation findings + PoC attempts + phase-specific instructions

**Phase Advancement Logic:**
- ingestion -> investigation: when description and repo index are available
- investigation -> formulation: when trigger hypothesis or vulnerable functions are found
- formulation -> verification: when a PoC file is written
- verification -> formulation: on failed verification (if < 3 attempts)
- verification -> investigation: on repeated failure (>= 3 attempts)

**Success Memory:** After verified PoC, saves a `feedback`-type memory recording the bug type, affected component, vulnerable functions, trigger hypothesis, and attempts needed.

### PoCVerificationCriteria (`stop_criteria.py`)

Custom `StopCriteria` that checks:
1. `state.is_verified()` -> SUCCESS (vul crashes, fix doesn't)
2. `poc_attempts >= max_attempts` -> AGENT_CONDITION (give up)

### CLI (`cli.py`)

Uses OpenAI-compatible API only (QitOS `OpenAIModel`). Supports:

```bash
python -m cybergym_agent run /path/to/task_dir \
    --model qwen3-coder-next \
    --api-key sk-xxx \
    --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
    --server http://localhost:8000 \
    --max-steps 30
```

### Local Runner (`run_local.py`)

Runs the agent locally with `HostEnv` (local filesystem, no Docker). Does code audit and PoC generation without verification. Uses the real CyberGym task generation (`prepare_arvo_files`) to prepare task directories.

Two modes:

**Mode A: Generate from raw CyberGym data directory** (recommended)

```bash
python -m cybergym_agent.run_local \
    --task-id arvo:3938 \
    --data-dir /path/to/repos/data \
    --difficulty level1 \
    --model qwen3-coder-next \
    --api-key sk-xxx \
    --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
    --max-steps 30
```

This uses `CyberGymAdapter.from_data_dir()` which calls `cybergym.task.gen_task.generate_task()` to produce a proper task directory with `submit.sh` and `README.md`, then wraps it as a QitOS Task.

**Mode B: From an already-prepared task directory**

```bash
python -m cybergym_agent.run_local \
    --task-dir /path/to/task_output_dir \
    --model qwen3-coder-next \
    --api-key sk-xxx \
    --base-url https://dashscope.aliyuncs.com/compatible-mode/v1
```

Difficulty levels determine which files the agent receives:
- `level0`: repo-vul.tar.gz only
- `level1`: + description.txt
- `level2`: + error.txt (the output when the vulnerable binary runs with a PoC)
- `level3`: + patch.diff, repo-fix.tar.gz

The adapter automatically extracts `repo-vul.tar.gz` so the agent can read source code directly via `read_file`, `grep`, etc.

## Data Flow

**With Docker (cli.py):**
1. **CLI** reads task directory, creates `CyberGymAdapter` -> `Task` object
2. **Agent.init_state()** parses description, classifies bug type, loads memory, builds repo index
3. **Engine loop** (observe -> decide -> act -> reduce):
   - LLM receives system prompt + history + state
   - LLM emits `Decision` with tool calls
   - `ActionExecutor` runs tools inside Docker container
   - `agent.reduce()` updates state, advances phase, stores error traces
4. **Context pipeline** manages context window via four-level compaction
5. **SubmitPoCTool** POSTs PoC to CyberGym server for verification
6. **PoCVerificationCriteria** checks server response for differential behavior
7. On success, **CyberGymMemory** saves feedback for future tasks
8. **EngineResult** contains final state with PoC path and verification metadata

**Local mode (run_local.py):**
1. **Adapter** calls `generate_task()` from `cybergym.task` to prepare task directory
2. **Adapter** extracts `repo-vul.tar.gz` and wraps as QitOS `Task`
3. **Agent** runs with `HostEnv` (local filesystem, no Docker)
4. Agent reads source code, searches codebase, writes PoC file -- all locally
5. No `submit_poc` call (no server verification)
6. Agent finishes when `FinalResultCriteria` or `MaxStepsCriteria` triggers

## Configuration

| Parameter | Default | Description |
|---|---|---|
| `model` | `qwen3-coder-next` | LLM model identifier |
| `api_key` | (required) | API key for OpenAI-compatible endpoint |
| `base_url` | (required) | API base URL (e.g., DashScope) |
| `server_url` | `http://localhost:8000` | CyberGym verification server |
| `max_steps` | `30` | Maximum agent steps per task |
| `shell_timeout` | `60` | Shell command timeout in seconds |
| `memory_dir` | `<workspace>/.cybergym/memory` | Long-term memory directory |
| `global_memory_dir` | `~/.cybergym/memory` | Cross-project memory directory |

## Dependencies

- `qitos` -- Agent framework (Engine, StateSchema, Memory, History, ToolRegistry, DockerEnv, etc.)
- `httpx` -- HTTP client for CyberGym server submission
- `pyyaml` -- YAML frontmatter parsing for memory files
- `openai` -- OpenAI-compatible API client (used by QitOS OpenAIModel)
- `docker` -- Container management (used by QitOS DockerEnv)
