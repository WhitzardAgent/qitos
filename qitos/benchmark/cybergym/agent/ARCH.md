# Architecture

## System Overview

The CyberGym PoC Generation Agent is an autonomous agent built on the [QitOS](https://github.com/Qitor/qitos) framework. It receives a vulnerability description and a source code archive, then autonomously investigates the codebase, formulates a proof-of-concept input, and iterates on verification feedback.

```
                         +-----------------+
                         |  CyberGym Task  |
                         |  (description,  |
                         |   repo-vul.tar) |
                         +--------+--------+
                                  |
                                  v
                    +-------------+-------------+
                    |       CyberGymAdapter      |
                    |  (task -> QitOS Task)      |
                    |  parse harness, discover   |
                    |  corpus, detect strategy   |
                    +-------------+--------------+
                                  |
                                  v
+-----------+    +---------------+---------------+    +------------------+
|  LLM API  |<-->|         CyberGymAgent         |<-->|  ToolRegistry    |
| (Qwen etc)|    |  (AgentModule subclass)       |    |  (auto_short_    |
+-----------+    +---------------+---------------+    |   aliases=True)  |
                         |         |          |       +------------------+
            +------------+    +----+----+     +------------+
            |                 |         |                  |
   +--------+-------+  +-----+---+  +--+--------+  +-----+-----+
   | ContextHistory  |  | Memdir  |  |   State   |  |   Env     |
   | (4-level compact)|  | Memory  |  | (StateSch)|  | (Host/Dkr)|
   +-----------------+  +---------+  +-----------+  +-----------+
         |                                         
   +-----+--------+                                
   | PostCompact  |  Restores: vuln desc, PoC,    
   | Restorer     |  error trace, harness, best PoC
   +--------------+                                
```

## Engine Loop

The QitOS Engine drives the agent through an observe-decide-act-reduce loop:

```
for step in range(max_steps):
    observation = agent.observe(state)       # Build context from state
    decision   = agent.decide(observation)   # LLM generates Decision with tool calls
    action     = agent.act(decision)         # Execute tools via ToolRegistry
    state      = agent.reduce(state, obs, decision)  # Update state, advance phase
```

### Native Function Calling (QitOS Built-In)

When the model supports OpenAI-compatible function calling (e.g., Qwen models via the harness preset), the QitOS Engine handles everything automatically:

1. The harness preset stamps the model with `qitos_harness_metadata` (including `native_tool_call_preferred=True`)
2. The Engine reads this metadata and configures the correct protocol and parser
3. `HistoryMessage` supports `role="tool"` and `tool_calls`/`tool_call_id` fields for native tool call conversations
4. `_ModelRuntime` detects `tool_calls` in the response and converts them to `Decision.act(actions=...)` via `_decision_from_native_tool_calls()`
5. `_ActionRuntime` appends `role="tool"` messages with correct `tool_call_id` matching
6. `_ContextRuntime` manages context budget and truncates tool results to fit within the model's window

No custom multi-turn conversation tracking is needed -- the framework handles it end-to-end.

## Four-Phase State Machine (PhaseEngine)

The agent uses QitOS's `PhaseEngine` with declarative `PhaseSpec` and `TransitionRule` definitions:

```python
PhaseEngine(
    phases=[
        PhaseSpec(
            name="ingestion",
            max_steps=2,
            transitions=[
                TransitionRule(target="investigation",
                               condition=lambda s: bool(s.vulnerability_description)),
            ],
        ),
        PhaseSpec(
            name="investigation",
            max_steps=8,
            transitions=[
                TransitionRule(target="formulation",
                               condition=lambda s: bool(s.trigger_hypothesis or s.vulnerable_functions or s.vulnerable_files),
                               priority=10),
                TransitionRule(target="formulation",
                               force_at_step=7,  # Force earlier transition
                               priority=0),
            ],
        ),
        PhaseSpec(
            name="formulation",
            max_steps=15,
            transitions=[
                TransitionRule(target="verification",
                               condition=lambda s: bool(s.poc_path),
                               priority=10),
            ],
        ),
        PhaseSpec(
            name="verification",
            transitions=[
                # Discriminant failure: both crash -> re-investigate
                TransitionRule(target="investigation",
                               condition=lambda s: s.discriminant_failed and s.last_verification_result and not s.is_verified() and s.poc_attempts >= 2,
                               priority=15),
                # Normal failure: keep iterating
                TransitionRule(target="formulation",
                               condition=lambda s: s.last_verification_result and not s.is_verified() and s.poc_attempts < 5,
                               priority=10),
                # Exhausted attempts: re-investigate
                TransitionRule(target="investigation",
                               condition=lambda s: s.last_verification_result and not s.is_verified() and s.poc_attempts >= 5,
                               priority=5),
            ],
        ),
    ],
    initial_phase="ingestion",
    state_attr="current_phase",
)
```

Phase transitions are evaluated each step in `reduce()` via `phase_engine.advance(state, step)`. The engine checks conditions in priority order, then force-at-step rules, then max_steps budget.

### Phase Transitions

| From | To | Condition | Priority |
|---|---|---|---|
| Ingestion | Investigation | `vulnerability_description` is set | 10 |
| Investigation | Formulation | Findings available | 10 |
| Investigation | Formulation | Step >= 7 (forced) | 0 |
| Formulation | Verification | `poc_path` is set | 10 |
| Verification | Investigation | Discriminant failed + >= 2 attempts | 15 |
| Verification | Formulation | Not verified + < 5 attempts | 10 |
| Verification | Investigation | Not verified + >= 5 attempts | 5 |

### Step Budget Allocation

For a typical 30-step run:

| Phase | Steps | Purpose |
|---|---|---|
| Ingestion | 0-1 | Read description, submit.sh, discover corpus |
| Investigation | 2-7 | Read source, trace data flow, form hypothesis |
| Formulation | 7-12 | Write PoC draft using detected strategy |
| Verification | 12-30 | Submit, iterate with feedback, re-submit |

## Component Details

### CyberGymState (`state.py`)

Extends `qitos.core.state.StateSchema` with domain-specific fields organized in groups:

- **Vulnerability context**: `vulnerability_description`, `cve_id`, `bug_type`, `affected_component`
- **CyberGym metadata**: `task_id`, `agent_id`, `checksum`, `server_url`
- **Harness info**: `harness_info` (parsed from submit.sh), `corpus_files` (discovered fuzzing samples), `poc_strategy` (auto-detected)
- **Investigation findings**: `vulnerable_files`, `vulnerable_functions`, `input_entry_points`, `trigger_hypothesis`, `repo_index`
- **PoC iteration**: `poc_path`, `poc_attempts`, `last_error_trace`, `last_verification_result`
- **PoC quality tracking**: `best_poc_path`, `best_poc_score` (0=miss, 1=partial, 2=success), `discriminant_failed`, `crash_type`, `crash_location`
- **Phase tracking**: `current_phase` (managed by PhaseEngine)

Key methods:
- `is_verified()`: Checks vul_exit_code != 0 AND (fix_exit_code == 0 or they differ)
- `_update_best_poc(score)`: Tracks best PoC across iterations for regression protection

### PoC Strategy Auto-Detection

The agent detects the optimal PoC generation strategy during `init_state()`:

| Strategy | Trigger | Method |
|---|---|---|
| `corpus_mutate` | Corpus/sample files found | Copy and modify existing files |
| `binary_python` | Binary format keywords in description | Python with `struct.pack` |
| `hex` | When hex encoding is more natural | `xxd -r` or `printf` |
| `text` | Default / text-based bugs | `write_file` directly |

### Harness-Aware Ingestion

During ingestion, the agent:

1. Reads `submit.sh` to understand the verification mechanism (binary path, input format)
2. Discovers fuzzing corpus directories (`fuzzing/corpus/`, `seeds/`, `testcases/`)
3. Discovers sample input files (images, archives, PDFs, etc.) in the repo
4. Stores harness info and corpus file list in state for use by formulation phase

### Verification Feedback Loop

When a PoC is submitted via `submit_poc`, the server returns:

```json
{
  "vul_exit_code": 1,
  "fix_exit_code": 0,
  "poc_id": "...",
  "vul_stderr": "AddressSanitizer: heap-buffer-overflow ...",
  "fix_stderr": "",
  "vul_stdout": "",
  "fix_stdout": ""
}
```

The agent processes this in `_process_action_result()`:

1. **Parse sanitizer output**: Extract `crash_type` (heap-buffer-overflow, use-after-free, etc.) and `crash_location` (file:line) from `vul_stderr`
2. **Score the PoC**:
   - Score 2: `vul_exit != 0` AND `fix_exit == 0` -- SUCCESS
   - Score 1: `vul_exit != 0` AND `fix_exit != 0` -- Discriminant failure (too aggressive)
   - Score 0: `vul_exit == 0` -- Miss
3. **Track best PoC**: Update `best_poc_path` and `best_poc_score` if this is the best result so far
4. **Set discriminant flag**: When fix_exit != 0, set `discriminant_failed = True` which triggers re-investigation
5. **Generate targeted error message**: Include discriminant-specific guidance in `last_error_trace`

### Discriminant-Aware Phase Transitions

The verification phase has three transition rules with different priorities:

1. **Priority 15 (Discriminant failure)**: When the PoC crashes both versions AND we've tried >= 2 times, redirect to **investigation** to re-examine the specific vulnerability. The key insight: a PoC that crashes both versions is too aggressive -- it needs to target only the vulnerable code path.

2. **Priority 10 (Normal iteration)**: When the PoC doesn't work but we haven't exhausted attempts (< 5), go back to **formulation** to try a different approach.

3. **Priority 5 (Exhausted)**: After 5+ failed attempts, go back to **investigation** to re-think the approach entirely.

### Long-Term Memory (QitOS `MemdirMemory`)

Uses QitOS's built-in `MemdirMemory` (`qitos.kit.memory.memdir_memory`) which implements the memdir protocol over the `Memory` ABC. Four memory types stored as Markdown with YAML frontmatter:

| Type | Directory | Purpose |
|---|---|---|
| `user` | `~/.cybergym/memory/user/` | Global preferences |
| `feedback` | `<workspace>/.cybergym/memory/feedback/` | Verified behavioral rules (PoC strategies per bug type) |
| `project` | `<workspace>/.cybergym/memory/project/` | Task-specific knowledge |
| `reference` | `<workspace>/.cybergym/memory/reference/` | External resource pointers |

After successful PoC generation, the agent saves a `feedback`-type memory recording the effective strategy for that bug type.

### Context Pipeline (`context.py`)

Four-level progressive compaction built on `CompactHistory`:

1. **Snip** -- Replace old tool/observation messages with `[Old tool result content cleared]` markers. Keeps last 4 compressible messages. No LLM call.
2. **MicroCompact** -- Compress long messages to preview + tail. No LLM call.
3. **Collapse** -- Proactive trigger at 90% context utilization. Snips + force-compacts. No LLM call.
4. **AutoCompact** -- LLM-based summarization of older rounds with CyberGym-specific summary prompt.

Additional components:
- **CompactionCircuitBreaker** -- Stops compaction after 3 consecutive failures
- **PostCompactRestorer** -- Re-injects vulnerability description, current PoC draft, last error trace, harness info, and best PoC path after compaction

### Tool Registry

The agent registers tools from multiple sources into `ToolRegistry(auto_short_aliases=True)`:

1. **CodingToolSet** (43 tools): `run_command`, `read_file`, `write_file`, `file_edit`, `glob`, `grep`, `search`, etc.
2. **TaskToolSet** (5 tools): `task_create`, `task_list`, `task_get`, `task_update`, `task_append_note`
3. **SecurityAuditToolSet** (8+ tools): `audit_config_scan`, `audit_hotspots`, `audit_secret_scan`, etc.
4. **SubmitPoCTool** (1 tool): `submit_poc`

With `auto_short_aliases=True`, the registry automatically registers short-name and `=`-separated aliases, handling models that drop toolset prefixes.

### CyberGymAdapter (`adapter.py`)

Converts CyberGym task directories to QitOS `Task` objects:

- Reads `description.txt`, `submit.sh`, `README.md` from the task directory
- Extracts `task_id` from `submit.sh`
- Computes SHA-256 checksum: `sha256(task_id + agent_id + salt)`
- Extracts `repo-vul.tar.gz` so the agent can read source code directly

### Mock Server (`mock_server.py`)

A standalone HTTP server that mimics the CyberGym verification server for testing:

- Supports `POST /submit-vul` endpoint
- Runs the PoC against a specified vulnerable binary (if available)
- Falls back to heuristic-based mock responses (larger PoCs = more likely to trigger)
- Returns the same JSON structure as the real server including `vul_stderr`/`fix_stderr`

## Data Flow

### Local Mode (run_local.py)

```
1. Adapter.from_data_dir() -> Task object
2. _create_llm() -> OpenAICompatibleModel (via build_model_for_preset)
   └─ Model is stamped with qitos_harness_metadata
3. CyberGymAgent(llm, workspace, ...) -> agent with:
   ├─ ToolRegistry(auto_short_aliases=True)
   ├─ MemdirMemory(memory_dir, global_memory_dir)
   ├─ CyberGymContextHistory(llm, CompactConfig(...))
   └─ PhaseEngine with discriminant-aware transitions
4. init_state() -> CyberGymState with:
   ├─ harness_info (parsed from submit.sh)
   ├─ corpus_files (discovered in repo)
   ├─ poc_strategy (auto-detected)
   └─ PhaseEngine initial phase
5. agent.run(task, env=HostEnv, context_config=ContextConfig(...)) -> EngineResult
   └─ Engine auto-detects protocol/parser from llm.qitos_harness_metadata
6. Engine loop: observe -> decide -> act -> reduce (x max_steps)
   ├─ PhaseEngine.advance(state, step) in reduce()
   ├─ _process_action_result() with discriminant-aware scoring
   ├─ ToolCallLoopDetector checks for repeated calls
   └─ ContextRuntime manages overflow protection
```

### Docker Mode (cli.py)

Same as local mode, but uses `CyberGymEnv` (Docker) instead of `HostEnv`, and the `submit_poc` tool can reach the verification server.

## Key Design Decisions

1. **Declarative PhaseEngine**: Phase transitions are defined declaratively with `PhaseSpec` and `TransitionRule`. Step-based forcing (`force_at_step=7`) prevents the model from investigating indefinitely. This replaced a custom `_advance_phase()` method that had a circular dependency bug.

2. **Framework-native tool calling**: QitOS's Engine handles native function calling end-to-end -- from detecting `tool_calls` in model responses to recording `role="tool"` messages with correct `tool_call_id` matching. No custom multi-turn conversation tracking is needed.

3. **Auto short-name aliases**: `ToolRegistry(auto_short_aliases=True)` eliminates "tool not found" errors when models call tools by short names or use `=` separators.

4. **Harness-aware ingestion**: The agent reads `submit.sh` during ingestion to understand the verification mechanism. This follows the pattern observed in 100% of successful Claude Code runs.

5. **Corpus-aware PoC bootstrapping**: 77% of successful runs leverage existing corpus/sample files. The agent discovers these during ingestion and suggests the `corpus_mutate` strategy.

6. **Discriminant-aware verification**: When the PoC crashes both vulnerable and fixed binaries, the agent recognizes this as a discriminant failure and redirects to re-investigation rather than blindly iterating on the same approach.

7. **Post-compact restoration**: After AutoCompact, critical context (vulnerability description, current PoC, last error, harness info, best PoC) is re-injected as system messages so the agent doesn't lose its working state.

8. **Harness preset auto-integration**: `build_model_for_preset()` stamps the model with `qitos_harness_metadata` that the Engine reads to auto-configure protocol, parser, and tool delivery. No manual `parser=`/`protocol=` passing needed.

9. **PoC quality tracking**: `best_poc_path` and `best_poc_score` prevent regression during iteration. The agent always knows which PoC performed best, even if subsequent attempts are worse.

10. **Crash feedback parsing**: Sanitizer output is parsed to extract crash type and location, providing the model with concrete feedback about what happened rather than just exit codes.
