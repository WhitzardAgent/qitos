# CyberGym Context Retention Alignment Design

**Date:** 2026-04-21  
**Scope:** `qitos/benchmark/cybergym/agent` single-agent runtime  
**Goal:** Reduce repeated file rereads after old tool results are snipped by aligning CyberGym's context-retention behavior with Claude Code's proven pattern: acknowledge loss, externalize important facts early, and keep a small stable working-memory block visible every turn.

## Problem

The current CyberGym single-agent runtime already improved tool discipline with `READ / BASH / WRITE`, but it still repeatedly rereads files after the first few candidate attempts.

The main reason is not simple indecision. It is a memory-carrier mismatch:

- old `tool` / `observation` content is snipped aggressively
- native tool-call history is trimmed to recent rounds
- the agent does not have a stable replacement carrier for critical facts learned from earlier reads

As a result, the model remembers that it previously inspected a path, but can no longer see the content. It rereads the file to restore certainty.

This is especially damaging in CyberGym because:

- the project under attack is static within a task
- the most important code facts are usually few
- repeated rereads waste both steps and budget after the first candidate miss

## Current QitOS CyberGym Retention Pipeline

The current stack has four layers:

1. **Snip**
   - Old `tool` / `observation` messages are replaced with `[Old tool result content cleared]`
   - Keeps the most recent `4` compressible messages
   - Source: `qitos/benchmark/cybergym/agent/context.py`

2. **MicroCompact**
   - Long messages are preview-compacted
   - Agent config currently uses:
     - `compact_long_messages_over_chars=600`
     - `microcompact_preview_chars=180`
     - `summary_max_chars=2000`
     - `keep_last_rounds=3`
     - `keep_last_messages=10`
     - `warning_ratio=0.75`
   - Source: `qitos/benchmark/cybergym/agent/agent.py`

3. **Collapse**
   - Proactive collapse at `90%` budget utilization
   - Source: `qitos/benchmark/cybergym/agent/context.py`

4. **AutoCompact**
   - LLM-based summarization through `CompactHistory`
   - Post-compact restorer then adds back selected state such as description, current PoC draft, last error trace, harness info, and best PoC

Separately, native tool-call history is trimmed to recent rounds in `qitos/engine/_model_runtime.py`.

### Observed Failure Mode

In the recent `arvo:15003` smoke run:

- heavy summary/collapse did **not** trigger
- but old tool results were still snipped
- the model repeatedly returned to `READ` because the earlier file content was no longer visible

So the immediate problem is not "context overflow." It is "always-on information loss without durable replacement."

## Claude Code Comparison

Claude Code does not solve this by keeping everything forever.

It also clears old tool results and uses compaction aggressively. But it differs in three important ways:

1. **It explicitly tells the model that old tool results will disappear**
   - Prompt includes a dedicated warning that old tool results will be automatically cleared while recent ones stay
   - Prompt also instructs the model to write down important information it might need later

2. **It has durable memory carriers**
   - `tool_use_summary`
   - `compact_boundary`
   - session-memory compaction
   - `MEMORY.md` / entrypoint memory

3. **It clears later and with clearer boundaries**
   - time-based microcompact default:
     - `gapThresholdMinutes = 60`
     - `keepRecent = 5`
   - API context-management defaults:
     - `DEFAULT_MAX_INPUT_TOKENS = 180000`
     - `DEFAULT_TARGET_INPUT_TOKENS = 40000`

The relevant lesson is not "copy all of Claude Code." The relevant lesson is:

> Old raw tool results may disappear, so important facts must be externalized into a stable working-memory layer before that happens.

## Design Goals

1. Keep the CyberGym runtime single-agent.
2. Do not introduce a new memory subsystem or new agents.
3. Preserve the current `READ / BASH / WRITE` tool model.
4. Keep the design close to Claude Code:
   - acknowledge tool-result clearing
   - force important information to be externalized
   - keep a small stable memory block in prompt context
5. Optimize for single-task static projects:
   - since the vulnerable project does not change during a task, a small project index and stable file memory are valuable and low-risk

## Proposed Design

### 1. Add a Small Durable Working Memory to State

Add a compact task-scoped working-memory structure to `CyberGymState`.

It should hold only stable, high-value facts:

- `project_index`
  - important parser paths
  - seed/sample paths
  - field-definition paths
- `code_facts`
  - file/function/constraint observations that are likely to be reused
- `feedback_facts`
  - the most important facts extracted from submission feedback

This is not a full note-taking system. It is the replacement carrier for facts that should survive snip.

### 2. Make the Prompt Explicit About Tool-Result Clearing

Align with Claude Code by telling the model:

- older tool results may be cleared later
- if a read reveals information needed for later iterations, it must be captured in the task working memory
- it must not assume the original read output will remain visible

This should live in the stable system prompt, not just transient observation text.

### 3. Keep a Stable Working-Memory Block Visible in Observation

Every turn, the observation packet should include a short Markdown section containing:

- project index summary
- durable code facts
- durable feedback facts

This block should be small and stable. It is the "always visible replacement" for older read results.

### 4. Update Durable Facts Only at High-Value Moments

Do not summarize every tool result.

Update durable facts only when:

- a `READ` reveals stable structural information
- a search or repo bootstrap reveals an important path worth keeping
- a `submit_poc` result reveals a durable feedback fact

This keeps the system close to Claude Code's "externalize important information" behavior rather than turning every turn into a summarization exercise.

### 5. Reuse Existing Evidence Index Instead of Inventing a New Index System

The repo is static during a task, and the current runtime already has `evidence_index`.

Instead of creating a separate indexing subsystem:

- normalize and surface the existing `evidence_index` as part of durable working memory
- add only the missing code-fact / feedback-fact layer

This keeps the implementation small and avoids duplicate representations.

### 6. Make Durable Facts Visible in Trace

Each step sidecar should record the working-memory block in `context.json` and summary output so debugging is easy:

- what the model knew persistently
- what it had to reread
- whether the working-memory block actually reduced rereads

## Data Model

Add these fields to `CyberGymState`:

- `durable_project_memory: Dict[str, Any]`
  - normalized long-lived task facts
- `durable_code_facts: List[str]`
  - short, stable code constraints / entrypoints / file-function facts
- `durable_feedback_facts: List[str]`
  - short, stable feedback-derived facts

Guidelines:

- keep entries short and textual
- deduplicate aggressively
- cap each list to a small number of entries
- prefer exact paths, function names, and parser constraints over prose

## Update Policy

### Project Memory

Populate once during bootstrap or refresh when `evidence_index` changes.

Keep:

- parser paths
- seed paths
- field paths
- a short repo summary

### Code Facts

Update when a `READ` clearly reveals:

- the relevant parser entrypoint
- the field or record that must be malformed
- the minimal structural constraint needed for the next candidate

### Feedback Facts

Update when `submit_poc` reveals:

- a parser reject string worth preserving
- a crash class
- a location or stage hint
- a clear "too short / too broad / wrong format" signal

## Prompt Design

Add a dedicated system-prompt section similar in spirit to Claude Code's function-result-clearing guidance:

- old file-read results may later be cleared from context
- if a read reveals something likely to matter later, capture it in working memory immediately
- do not rely on rereading the same file unless the working memory is truly insufficient

Observation should include a Markdown section such as:

- `## Working Memory`
- `### Project Index`
- `### Durable Code Facts`
- `### Durable Feedback Facts`

This gives the model a predictable place to look before rereading.

## Trace Design

Extend step sidecars so `context.json` and `trace_summary.jsonl` include:

- durable project memory summary
- durable code facts
- durable feedback facts

This makes the retention chain inspectable without opening the full conversation transcript.

## Non-Goals

This design intentionally does **not** introduce:

- multi-agent memory management
- automatic summarization for every tool result
- cross-task exploit knowledge transfer
- a separate evidence graph subsystem
- radical changes to QitOS compaction internals

## Expected Outcome

If this works, the agent should:

- reread files less often after the first candidate miss
- rely more on persistent working memory for stable parser facts
- stay closer to `candidate -> submit -> feedback -> mutate`
- remain easier to debug because the retained facts are explicit in trace sidecars

## Implementation Scope

Minimal implementation touches:

- `qitos/benchmark/cybergym/agent/state.py`
- `qitos/benchmark/cybergym/agent/agent.py`
- targeted tests for prompt/context behavior

No changes are required to the underlying generic `CompactHistory` framework for the first slice.
