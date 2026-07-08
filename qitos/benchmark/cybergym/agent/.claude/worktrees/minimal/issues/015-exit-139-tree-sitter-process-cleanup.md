# Issue 015: Exit Code 139 from tree-sitter SIGSEGV During Process Cleanup

## Status: ACCEPTED (cosmetic — agent data is complete)

## Problem

In V9 exploration-only batch, 41/100 tasks exited with code 139 (SIGSEGV).
However, unlike the `index_file()` SIGSEGV documented in issue #013, these
crashes occur **during Python process exit**, not during indexing. The
tree-sitter C extension's garbage collection / cleanup triggers the segfault
when the Python interpreter is shutting down.

The practical impact is **zero**: all 41 tasks completed their full 15 exploration
steps, produced sink candidates, and wrote state snapshots. The exit code 139
is misleading — the batch runner reports a "crash" but the agent's work is intact.

## Evidence

V9 run on 146 server:
- 41/100 tasks with exit=139
- All 41 have complete `state_*.json` snapshots (step 15)
- All 41 have `sink_candidates` in their final state
- No task stopped early due to the segfault

Analysis of run.log timing: the SIGSEGV occurs after the last STEP banner,
during `atexit` handlers or garbage collection. tree-sitter's C `Tree` objects
hold pointers that become invalid when the Python GC collects them in a
non-deterministic order during interpreter shutdown.

## Current Mitigation

1. **Subprocess isolation** (`index_file_isolated()`): prevents SIGSEGV during
   indexing from killing the main process. This is working correctly (issue #013).

2. **Batch runner**: `run_exploration_batch.sh` treats exit=139 as a non-fatal
   completion. The analyze step reads state snapshots regardless of exit code.

3. **Analysis pipeline**: sink recall evaluation reads from `state_*.json` and
   falls back to run.log parsing, so exit=139 tasks are fully counted.

## Potential Fix (not recommended for now)

To suppress the exit=139 entirely:

1. **Disable tree-sitter GC during shutdown**: Register an `atexit` handler that
   explicitly frees tree-sitter trees before the interpreter begins cleanup.
   Risk: complex, may not cover all cases, could introduce new crashes.

2. **Suppress the signal**: Catch SIGSEGV in a signal handler and force exit(0).
   Risk: unsafe — a real SIGSEGV during active computation would be silently
   swallowed.

3. **Use `os._exit(0)` in atexit**: Force a clean exit before GC runs.
   Risk: skips all cleanup, may leave temp files or corrupt state.

Given that the crash is cosmetic (agent work is complete), none of these are
worth the risk. The batch runner and analysis pipeline already handle it correctly.

## Monitoring

If future runs show tasks stopping early (e.g., exit=139 at step 3 instead of
step 15), this would indicate a different SIGSEGV pattern and should be
investigated as a separate issue.

## Priority

**Low** — No functional impact. The exit code is misleading but all data is
preserved. Only worth fixing if the batch runner is changed to treat exit=139
as a hard failure.
