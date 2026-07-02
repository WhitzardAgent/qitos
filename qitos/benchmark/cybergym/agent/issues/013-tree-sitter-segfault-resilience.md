# Issue 013: tree-sitter SIGSEGV Crash Resilience

## Status: FIXED (subprocess isolation + per-function try/except)

## Problem

When running the CyberGym agent on 100 exploration-only tasks, 41 out of 100
exited with code 139 (SIGSEGV). Root cause is tree-sitter C extension crashing
during AST node traversal in `index_file()` — specifically in `walk()`,
`child_by_field_name()`, `parsed.text()`, `expr_ir()`, and
`extract_callsite_constraints()` calls. The crash is at the C level and bypasses
Python's exception mechanism, so try/except cannot catch it.

In the full PoC-generation pipeline (launch-146.sh), this is **critical**:
a single tree-sitter segfault kills the entire Python process for that task,
and if the batch runner doesn't handle it, the whole batch can stall or
silently lose results.

## Evidence

Run `0701-v7-exp-vague-100-0701-2148` on 146 server:
- 41/100 tasks crashed with exit=139 (SIGSEGV)
- Crashes occur mid-run (steps 5+), not at startup
- Direct `parser.parse()` succeeds — crash is in post-parse node traversal
- 30% correlation with `record_sink_candidate` timing suggests the crash
  occurs when `AnalysisService.index_repository()` is triggered

## Fix Applied

### 1. Subprocess isolation for `index_file()` (primary fix)

New function `index_file_isolated()` in `analysis/indexer.py`:
- Runs each file's indexing in a subprocess via `subprocess.run()`
- If subprocess exits with 139 (SIGSEGV) or 134 (SIGABRT), returns empty
  results with reason "sigsegv_crash" / "sigabrt_crash"
- If subprocess times out (default: 25% of total analysis budget per file),
  returns empty results with reason "index_timeout"
- Parent process collects results from all successfully indexed files
- Non-crashing files provide full analysis info to the agent

New module `analysis/_index_worker.py`:
- Subprocess entry point: reads root+path from stdin JSON, runs `index_file()`,
  writes serialized results to stdout

`AnalysisService.index_repository()` updated to use `index_file_isolated()`
instead of `index_file()`.

### 2. Per-function try/except in `index_file()` (defense in depth)

The function processing loop (lines 163-219) now wraps each function in
try/except so that a Python-level exception in one function doesn't abort
the entire file. Crashed functions are logged and skipped.

### 3. Iterative walk() (previously applied)

`walk()` changed from recursive to iterative (explicit stack) to avoid
C-level stack overflows on deeply nested ASTs.

### 4. Tree reference preservation (previously applied)

`ParsedSource._tree_ref` holds a reference to the Tree object to prevent
GC from freeing the C-owned root_node during traversal.

All three had already made progress (steps 5+), meaning tree-sitter crashes
mid-run rather than at startup — it depends on which source file gets parsed.

## Required Fix

### 1. Signal handler fallback (Python level)

In `agent.py` or a shared utility, register a SIGSEGV handler that:
- Writes a crash marker to `.agent/crash_signal.json` with step number and partial state
- Allows the outer batch runner to detect the crash and record partial results
- Does NOT try to continue execution after SIGSEGV (unsafe)

```python
import signal, json, os

def _segfault_handler(signum, frame):
    crash_file = os.environ.get("CYBERGYM_TASK_TRACE_DIR", ".") + "/.agent/crash_signal.json"
    os.makedirs(os.path.dirname(crash_file), exist_ok=True)
    with open(crash_file, "w") as f:
        json.dump({"signal": signum, "step": getattr(frame, "f_locals", {}).get("step_count", -1)}, f)
    # Re-raise to let the process die cleanly
    signal.signal(signum, signal.SIG_DFL)
    signal.raise_signal(signum)

signal.signal(signal.SIGSEGV, _segfault_handler)
```

### 2. tree-sitter try/except wrapper (call-site level)

Wrap all tree-sitter parsing calls so that a crash in the C extension
doesn't propagate as an unhandled signal:

```python
def safe_tree_sitter_parse(source_bytes, parser, timeout=5):
    """Parse with tree-sitter, returning None on crash/timeout."""
    try:
        return parser.parse(source_bytes)
    except Exception:
        return None
    # For SIGSEGV: use subprocess isolation or signal handler above
```

For true SIGSEGV isolation, the most robust approach is to parse in a
subprocess:

```python
def safe_parse_subprocess(source_bytes, lang, timeout=5):
    """Parse in isolated subprocess; return None on any crash."""
    import subprocess, json
    try:
        result = subprocess.run(
            [sys.executable, "-m", "cybergym_agent.tree_sitter_worker", lang],
            input=source_bytes, capture_output=True, timeout=timeout,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass
    return None
```

### 3. Batch runner resilience (shell level)

In `run_exploration_batch.sh`, handle non-zero exit codes gracefully:

```bash
run_single() {
  ...
  local exit_code=$?
  if [[ ${exit_code} -eq 139 ]]; then
    echo "[CRASH] ${task_id} exit=139 (SIGSEGV — likely tree-sitter)"
    echo "${task_id},crash,sigsegv" >> "${RUN_ROOT}/_crashed.csv"
    # Still count as "attempted" — partial results may exist in .agent/
    return 0  # Don't block the batch
  fi
  echo "[DONE] ${task_id} exit=${exit_code}"
  return ${exit_code}
}
```

### 4. Graceful degradation in constraint extraction

When tree-sitter fails, fall back to regex-based extraction:

```python
def extract_constraints(source_path, language):
    tree = safe_tree_sitter_parse(...)
    if tree is None:
        # Fallback: regex-based extraction
        return regex_constraint_extract(source_path, language)
    return tree_sitter_constraint_extract(tree, source_path)
```

## Priority

**High** — Without this, any full batch run (100+ tasks) will lose ~3% of
results to silent crashes, and there's no recovery mechanism. The subprocess
isolation approach (option 2) is the most robust but adds latency; the signal
handler + batch runner fix (options 1+3) is the minimum viable fix.

## Assigned To

Technical expert — needs C extension debugging experience.
