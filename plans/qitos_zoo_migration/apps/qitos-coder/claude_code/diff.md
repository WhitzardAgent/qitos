# QitOS Coding Agent vs Real Claude Code — Gap Analysis

> Last updated: 2026-05-15  
> QitOS: `examples/real/claude_code/` + `qitos/`  
> Claude Code: `../claude-code/`

---

## Executive Summary

QitOS's coding agent has **functional infrastructure** (engine loop, 30+ tools, parsers, permissions, REPL) but lags behind the real Claude Code in three critical dimensions:

1. **Prompt & Tool Quality** (partially addressed in recent update) — system prompt now matches Claude Code's 7-section structure; per-tool prompts added; environment context injected
2. **Engine & Runtime Architecture** — sequential tool execution, no streaming tool execution, basic context management, no attachment system
3. **REPL/UX Experience** — no vim modes, no multi-line input, no structured diffs, no mid-stream tool call display, minimal keybindings

The most impactful gaps are **architectural** (streaming, concurrency, context management) rather than surface-level features. Closing these gaps requires engine-level changes, not just prompt tweaks.

---

## 1. Engine & Runtime Architecture

### 1.1 Streaming Architecture [P0 — Critical]

| Aspect | Claude Code | QitOS |
|--------|-------------|-------|
| **Granularity** | SSE event-level (content_block_start/delta/stop, tool_use input streaming) | Flat text delta callback |
| **Tool execution timing** | Tools begin executing as `tool_use` blocks complete during streaming | Tools execute only after full response parsed |
| **Thinking blocks** | Preserved with signatures, `thinking_delta` events | Stripped by `clean_stream_text()` |
| **Streaming markdown** | `StreamingMarkdown` with block-boundary incremental parsing | Raw `stdout.write` per delta |

**Claude Code** (`claude.ts`): Iterates `for await (const part of stream)` with per-event handling. `StreamingToolExecutor` starts tools as `content_block_stop` fires for tool_use blocks. Text deltas are rendered via `StreamingMarkdown` which splits at block boundaries — stable prefix memoized, only unstable suffix re-parsed.

**QitOS** (`_model_runtime.py:412-433`): If `stream_callback` is set and LLM has `stream()`, iterates chunks calling `callback(text)`. Accumulates full text into a string. No structural awareness — no content-block-level yields, no tool_use streaming, no thinking block handling.

**What to build**:
- Per-chunk event system: `StreamEvent(type="text_delta"|"tool_use_start"|"tool_input_delta"|"tool_use_complete"|"thinking_delta")`
- Streaming tool executor: start tool execution when `tool_use_complete` fires, don't wait for full response
- Streaming markdown renderer: incremental parsing with block-boundary splitting

### 1.2 Tool Execution Concurrency [P0 — Critical]

| Aspect | Claude Code | QitOS |
|--------|-------------|-------|
| **Read-only tools** | Run in parallel (up to 10 concurrent) | Sequential only |
| **Write tools** | Run exclusively (one at a time) | Sequential only |
| **Streaming execution** | Tools start before response finishes | Tools start after full response |
| **Concurrency classification** | `isConcurrencySafe(parsedInput)` per tool | No concept |

**Claude Code** (`StreamingToolExecutor.ts`): As each `tool_use` block completes, `addTool()` starts execution immediately. Read-only tools run in parallel. Non-safe tools wait. Results buffered and emitted in order. Max 10 concurrent (env `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`).

**QitOS** (`action_executor.py`): Strictly sequential. `if self.policy.mode == "parallel": raise NotImplementedError`. All actions execute one-by-one via `_execute_one()`.

**What to build**:
- `ToolConcurrencyClassifier`: classify tools as safe (Read, Glob, Grep) vs exclusive (Edit, Write, Bash)
- `ConcurrentActionExecutor`: run safe tools in parallel using `concurrent.futures.ThreadPoolExecutor`
- Integrate with streaming: start safe tools as their `tool_use` blocks complete mid-stream

### 1.3 Context Management [P0 — Critical]

| Aspect | Claude Code | QitOS |
|--------|-------------|-------|
| **Layers** | 7 (tool result budget, snip, microcompact, context collapse, autocompact, reactive compact, blocking limit) | 2 (window eviction + optional LLM summary) |
| **Tool result budget** | Per-message 200K chars, per-tool 50K, large results persisted to disk | Per-tool 8K chars, simple truncation |
| **Microcompact** | Cache-editing based removal of old tool results from prompt cache | None |
| **Context collapse** | Read-time projection, stored separately | None |
| **Reactive compact** | Post-hoc recovery on 413/prompt-too-long errors | None |
| **Compaction prompt** | Structured `<analysis>/<summary>` with 9 sections, NO_TOOLS preamble | Updated to match Claude Code format |

**Claude Code** (`query.ts:366-548`): At the start of each iteration, a 7-layer pipeline runs: (1) apply tool result budget, (2) snip compact, (3) microcompact, (4) context collapse, (5) autocompact, (6) check for reactive compact, (7) blocking limit check.

**QitOS** (`_EngineWindowHistory` + `CompactHistory`): Simple window of N messages with FIFO eviction. `CompactHistory` has microcompact (truncate old tool results) and summary compact (LLM summarization). No per-message budget, no reactive compact, no context collapse.

**What to build**:
- Increase `tool_result_max_chars` from 8K to 50K (aligned with Claude Code)
- Add per-message aggregate budget (200K chars)
- Add reactive compact: on API error indicating prompt too long, auto-compact and retry
- Tool result persistence to disk for large outputs (replace with preview + path)

### 1.4 Attachment System [P1 — Important]

| Aspect | Claude Code | QitOS |
|--------|-------------|-------|
| **File change tracking** | Diff attachments injected between turns | None |
| **Memory prefetch** | Async memory loading at turn start | None |
| **Skill discovery** | Async skill prefetch during streaming | None |
| **Queued commands** | Slash commands and notifications from message queue | None |
| **Tool use summary** | Async Haiku-generated summary of tool batch | None |
| **MCP refresh** | Tools refreshed between turns | None |

**Claude Code** (`query.ts:1547-1643`): Between tool execution and the next API call, an attachment injection phase runs: file edits since last turn generate diff attachments, memory prefetch completes, skill discovery results are consumed, queued commands are converted to messages.

**QitOS**: No attachment system. The engine flows decide → act → reduce → check_stop with no intermediate injection.

**What to build**:
- File change tracker: record all Edit/Write operations, generate diff summary for next turn
- Memory loading: at turn start, load `MEMORY.md` into context
- Message queue: allow slash commands and notifications to be injected between steps

### 1.5 Error Recovery [P1 — Important]

| Aspect | Claude Code | QitOS |
|--------|-------------|-------|
| **API errors** | Exponential backoff with model fallback | Generic recoverable/unrecoverable |
| **Streaming fallback** | Falls back to non-streaming on stream error | None |
| **Max output tokens** | Escalation from 8K → 64K with resume mid-thought | None |
| **Prompt too long** | Context collapse drain + reactive compact | ContextOverflowError, run stops |
| **Stall detection** | 90s idle watchdog, 30s stall detection | None |

**Claude Code** (`query.ts`, `claude.ts`): Multiple recovery strategies per failure type. `withRetry` for API errors. Streaming fallback on stream error. Max output tokens recovery with escalation. Prompt-too-long recovery with context compaction. Stream idle watchdog at 90s.

**QitOS** (`recovery.py`): `classify_exception()` categorizes errors as recoverable/unrecoverable. Up to `max_recoveries_per_run` (3) recoveries, then stops. No model fallback, no streaming fallback, no output token recovery, no stall detection.

**What to build**:
- Stream idle watchdog: timeout after 90s of no chunks, abort and retry
- Max output tokens recovery: on truncated response, send continuation message
- Prompt-too-long recovery: on context overflow, auto-compact and retry
- Retry with exponential backoff for API errors

### 1.6 Hook System [P2 — Nice to have]

| Aspect | Claude Code | QitOS |
|--------|-------------|-------|
| **Event types** | 20+ (PreToolUse, PostToolUse, UserPromptSubmit, SessionStart, etc.) | 6 (on_before_step, on_after_step, etc.) |
| **Execution** | Shell commands, HTTP callbacks, TypeScript functions | Internal Python callbacks only |
| **Input modification** | PreToolUse can modify tool input | No |
| **Output modification** | PostToolUse can modify tool result | No |
| **User configuration** | External `settings.json` | No external config |

**What to build** (lower priority):
- External hook configuration via `.qitos/hooks.json`
- Shell command execution for hooks
- PreToolUse hook with input modification support
- PostToolUse hook with output modification support

---

## 2. REPL & UX Experience

### 2.1 Input Handling [P1 — Important]

| Feature | Claude Code | QitOS | Priority |
|---------|-------------|-------|----------|
| Vim modes | INSERT/NORMAL with operators, motions, text objects | None | P2 |
| Multi-line input | Native with cursor tracking, shift+enter | Single-line `input()` only | P1 |
| Tab completion | Files, commands, agents, MCP, shell | Slash commands only | P1 |
| External editor | ctrl+x ctrl+e opens $EDITOR | None | P2 |
| Paste handling | Truncation at 10K chars, reference tracking | None | P2 |
| Input modes | prompt, bash (! prefix), mode cycling | Single mode | P2 |
| Image paste | ctrl+v/alt+v with `[Image #N]` chips | None | P3 |

**What to build**:
- Multi-line input: detect when input is incomplete (unclosed braces, backticks), continue on next line
- File path tab completion: complete paths relative to workspace
- `!` prefix for direct shell execution (already mentioned in session guidance prompt)

### 2.2 Output Rendering [P1 — Important]

| Feature | Claude Code | QitOS | Priority |
|---------|-------------|-------|----------|
| Markdown during streaming | StreamingMarkdown with incremental parsing | Plain text only | P1 |
| Syntax highlighting | Native Rust module + cli-highlight | Pygments via Rich | P2 |
| Structured diffs | FileEditToolDiff with line numbers, cache | Raw git diff in code block | P1 |
| Code block folding | CollapsedReadSearch, verbose toggle | None (truncation at 8 lines) | P2 |
| Tool result formatting | Per-tool specialized (Edit shows diff, Bash shows last N lines) | Generic one-liner per tool | P1 |
| Mid-stream tool calls | StreamingToolUse with loader animation | None (appears after full parse) | P2 |

**What to build**:
- Streaming markdown: use Rich's `Live` display for incremental markdown rendering during streaming
- Edit diff display: after Edit tool succeeds, show `difflib.unified_diff` of old vs new content
- Bash output truncation: show last 20 lines instead of 8, with total line count

### 2.3 Permission Dialog [P1 — Important]

| Feature | Claude Code | QitOS | Priority |
|---------|-------------|-------|----------|
| Per-tool UI | BashPermissionRequest, FileEditPermissionRequest (with diff), etc. | Generic `? ToolName(detail) [y/n]` | P1 |
| Options | Accept/Reject with feedback, Edit args | y/n only | P2 |
| Permission explanation | ctrl+e to show why permission was asked | None | P2 |
| Auto-classifier | Shimmer animation while classifying, auto-approve safe commands | Static ask/allow/deny | P2 |

**What to build**:
- Show Edit diff preview in permission prompt (old → new)
- Show command details for Bash permission (full command, not just description)
- Add "always allow" option (update permission rules)

### 2.4 Status & Progress [P2 — Nice to have]

| Feature | Claude Code | QitOS | Priority |
|---------|-------------|-------|----------|
| Spinner modes | Thinking/Reading/Editing/Tool-use with verb display | "Thinking..." only | P2 |
| Tips on spinner | Rotating tips during long waits | None | P3 |
| Cost tracking | Dollar cost, context %, rate limits | Token counts only | P2 |
| Progress reporting | OSC 9;4 for iTerm2/Ghostty | None | P3 |

**What to build**:
- Verb-based spinner: detect current activity from last tool name, show "Reading...", "Editing...", etc.
- Track dollar cost per model (need pricing data)

### 2.5 Slash Commands [P2 — Nice to have]

| Claude Code (70+) | QitOS (9) | Priority to add |
|-------------------|-----------|-----------------|
| `/compact` | `/compact` | — (done) |
| `/cost` | `/cost` | — (done) |
| `/undo` | `/undo` | — (done) |
| `/model` | `/model` | — (done) |
| `/diff` | `/diff` | — (done) |
| `/status` | `/status` | — (done) |
| `/clear` | `/clear` | — (done) |
| `/help` | `/help` | — (done) |
| `/config` | — | P2 |
| `/memory` | — | P2 |
| `/doctor` | — | P2 |
| `/review` | — | P3 |
| `/resume` | — | P2 |
| `/permissions` | — | P2 |
| `/init` | — | P3 |
| `/commit` | — | P2 |
| `/pr` | — | P2 |
| `/skills` | — | P3 |

**What to build** (most impactful):
- `/memory`: show/edit memory entries
- `/permissions`: show/edit permission rules
- `/commit`: guided git commit with HEREDOC format
- `/pr`: guided PR creation with `gh`

### 2.6 Terminal & Keyboard [P2 — Nice to have]

| Feature | Claude Code | QitOS | Priority |
|---------|-------------|-------|----------|
| Alternate screen | DEC 1049 with mouse tracking | Main screen only | P2 |
| Synchronized output | DEC 2026 for flicker-free redraw | None | P3 |
| ctrl+l clear | Clears and redraws | No screen clear | P2 |
| ctrl+c interrupt | AbortController, graceful shutdown | KeyboardInterrupt catch | P1 |
| Keybinding remapping | `~/.claude/keybindings.json` | None | P3 |

**What to build**:
- ctrl+l to clear screen and redraw status
- Better ctrl+c handling: abort current tool/step, don't exit REPL
- Double ctrl+c to exit

---

## 3. Tools & Capabilities

### 3.1 Missing Critical Tools [P0]

| Tool | What it does | Why it matters |
|------|-------------|----------------|
| **WebSearch** | Server-side web search with citation support | Models cannot access current information without it |
| **Agent (working)** | Spawn sub-agents for parallel work | `agent_spawn` is currently a **stub** — returns `{"spawned": False}` |
| **Plan mode enforcement** | Actually restrict tools in plan mode | Currently cosmetic — model can still write files in "plan mode" |
| **MCP client** | Dynamic tool discovery and invocation | MCP tools are **stubs** that read from pre-injected dict |

### 3.2 Missing Important Tools [P1]

| Tool | What it does | Why it matters |
|------|-------------|----------------|
| **SkillTool** | Slash-command/skill invocation system | No reusable workflows |
| **SendMessageTool** | Inter-agent communication | No multi-agent coordination |
| **TeamCreateTool** | Multi-agent swarm management | No team workflows |
| **TaskStopTool** | Stop running agent tasks | No way to cancel background agents |
| **TaskOutputTool** | Read output from completed agents | No way to retrieve async results |
| **ToolSearchTool** | Deferred tool discovery for large tool pools | Not needed yet (tool count manageable) |
| **Cron (working)** | Actual scheduled task execution | All 3 cron tools are **stubs** |

### 3.3 Missing Nice-to-have Tools [P2]

| Tool | Description |
|------|-------------|
| NotebookEdit (safety) | Read-before-edit, staleness check, cell ID generation |
| ConfigTool | Runtime configuration read/write |
| PowerShellTool | Windows PowerShell execution |
| BriefTool | Structured user-facing messages |
| SleepTool | Explicit delay for proactive workflows |

### 3.4 Agent/Sub-agent System [P0 — Critical]

| Aspect | Claude Code | QitOS |
|--------|-------------|-------|
| **Agent spawning** | `AgentTool` spawns child query loops | `agent_spawn` is a stub |
| **Fork mode** | Fork self with inherited context + prompt cache | Not supported |
| **Background execution** | `run_in_background` with completion notifications | Not supported |
| **Worktree isolation** | `isolation: "worktree"` for isolated git worktree | `enter_worktree` exists but not wired to agents |
| **Inter-agent messaging** | SendMessage with team broadcast, shutdown requests | Not supported |
| **Sub-agent types** | Explore, Plan, general-purpose, verification, guide | Explore, Plan, Guide defined but not callable from model |

**What to build**:
- Make `agent_spawn` actually work: create a child Engine with the sub-agent's toolset, run it, return results
- Add `run_in_background` support: run agent in thread, notify on completion
- Wire `enter_worktree` into agent spawning for isolation

### 3.5 Plan Mode [P0 — Critical]

| Aspect | Claude Code | QitOS |
|--------|-------------|-------|
| **Permission enforcement** | Switches to plan permission mode, blocks all writes | Sets metadata flag only — **no enforcement** |
| **Plan persistence** | Writes plan to disk file | No persistence |
| **User approval** | ExitPlanMode requires user confirmation | No approval flow |
| **Plan display** | Full plan shown in approval dialog | No structured display |

**What to build**:
- In `ActionExecutor`, check `state.metadata.get("mode") == "plan"` before executing write/bash tools → deny
- Write plan content to `.qitos/plan.md` when exiting plan mode
- Add user confirmation prompt before exiting plan mode

### 3.6 MCP Integration [P0 — Critical]

| Aspect | Claude Code | QitOS |
|--------|-------------|-------|
| **Transport** | Stdio, SSE, StreamableHTTP, WebSocket | None (pre-injected dict) |
| **Tool discovery** | Dynamic via `ListToolsResultSchema` | None |
| **Resource access** | Live via `ListResourcesResultSchema` | Reads from `runtime_context["mcp_resources"]` |
| **Dynamic injection** | MCP tools added to tool pool at runtime | None |

**What to build**:
- MCP client with stdio transport (most common)
- Dynamic tool discovery and injection into tool registry
- Resource listing and reading via MCP protocol

---

## 4. Priority Roadmap

### Phase A: Critical Architecture (P0)

These changes are required for the agent to be genuinely usable for real work:

| # | Task | Files | Impact | Effort | Status |
|---|------|-------|--------|--------|--------|
| A1 | **Tool execution concurrency** | `action_executor.py` | Major latency reduction | High | Done |
| A2 | **Plan mode enforcement** | `action_executor.py`, `engine.py`, `agent.py` | Safety — model can't write in plan mode | Low | Done |
| A3 | **Agent spawning (make it work)** | `coding_impl.py` `agent_spawn`, new `subagents.py` | Multi-step task delegation | Medium | Done |
| A4 | **WebSearch tool** | New `qitos/kit/tool/browser/` or `coding_impl.py` | Access to current information | Medium | Pending |
| A5 | **Streaming tool execution** | `_model_runtime.py`, `engine.py` | Start tools before response completes | High | Pending |
| A6 | **Context management improvements** | `states.py`, `_action_runtime.py`, `_control_runtime.py` | Long-session quality | Medium | Done |
| A7 | **MCP client** | New `qitos/kit/mcp/` | Extensibility via external tools | High | Pending |

### Phase B: Important Features (P1)

| # | Task | Files | Impact | Effort |
|---|------|-------|--------|--------|
| B1 | **Attachment system** | `engine.py` | Model informed about file changes | Medium |
| B2 | **Multi-line input** | `core.py` | Paste code, write multi-line prompts | Medium |
| B3 | **File path tab completion** | `core.py` | Faster input | Low |
| B4 | **Edit diff display** | `formatter.py` | See what changed before approving | Low |
| B5 | **Streaming markdown** | `markdown.py`, `core.py` | Better output during generation | Medium |
| B6 | **Skill system** | New `qitos/kit/skill/` | Reusable workflows | Medium |
| B7 | **Task persistence** | `coding_impl.py` | Tasks survive session end | Low |
| B8 | **Error recovery improvements** | `recovery.py`, `_model_runtime.py` | Robustness on API errors | Medium |
| B9 | **Cron (make it work)** | `coding_impl.py` cron tools | Scheduled tasks | Medium |
| B10 | **Permission dialog upgrade** | `core.py` | Show diffs, command details | Low |

### Phase C: Polish (P2)

| # | Task | Files | Impact | Effort |
|---|------|-------|--------|--------|
| C1 | Vim input mode | `core.py` or new `input.py` | Power user experience | High |
| C2 | Verb-based spinner | `spinner.py` | Better status awareness | Low |
| C3 | `/commit` and `/pr` commands | `commands.py` | Guided git workflows | Low |
| C4 | `/memory` command | `commands.py` | Memory management | Low |
| C5 | Cost tracking (dollars) | `core.py` | Budget awareness | Low |
| C6 | ctrl+c abort current step | `core.py` | Don't lose session on interrupt | Low |
| C7 | Hook system expansion | `hooks.py` | User extensibility | Medium |
| C8 | Notebook safety features | `notebook.py` | Better notebook editing | Low |

---

## 5. Already Completed (Recent Update)

| Item | Status |
|------|--------|
| System prompt rewrite (7-section structure) | Done |
| Per-tool prompt descriptions (8 tools) | Done |
| Tool prompts wired into protocol schema renderers | Done |
| Environment context injection (cwd, git, platform) | Done |
| Git status injection | Done |
| Date injection | Done |
| Sub-agent prompt upgrades | Done |
| Compaction prompt upgrade (structured 9-section format) | Done |
| Memory prompt section | Done |
| Session guidance section | Done |
| Kimi tool call parser | Done |
| Static/dynamic prompt split | Done |
| **Plan mode enforcement** (permission pipeline + RBW enforcer wired to engine) | Done |
| **Context management improvements** (8K→50K tool result limit, per-message 200K budget, reactive compact on overflow) | Done |
| **Tool execution concurrency** (parallel read-only tools: Read, Glob, Grep, WebFetch) | Done |
| **Agent spawning** (agent_spawn now creates real sub-agents: explore, plan, general) | Done |
| **API context overflow recovery** (detects OpenAI/Anthropic context_length_exceeded, auto-compacts and retries) | Done |

---

## 6. Key Insight

The biggest gap is **not** prompts or tools — it's **engine architecture**. The real Claude Code's advantages come from:

1. **Streaming tool execution** — starting tools before the response finishes saves 5-30 seconds per turn
2. **Concurrent tool execution** — running 5 Glob/Grep/Read calls in parallel instead of sequentially saves another 5-15 seconds
3. **Multi-layer context management** — keeping 200K of tool results instead of 8K, with reactive compaction on overflow
4. **Attachment injection** — the model knows about file changes, memory updates, and queued commands between turns

These are all engine-level changes that require rethinking the `Engine.run()` loop, `ActionExecutor`, and `_ModelRuntime`. The prompt and tool quality improvements we've made will have limited impact until these architectural gaps are closed.
