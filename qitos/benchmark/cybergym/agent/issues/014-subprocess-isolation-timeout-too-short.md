# Issue 014: Subprocess Isolation Per-File Timeout Too Short

## Status: FIXED (timeout increased)

## Problem

In V10 exploration-only batch run, 39/100 tasks failed with `unrecoverable_error`
after only 2 steps. Root cause: the per-file timeout in `AnalysisService.index_repository()`
was set to `max(0.5, min(2.0, remaining))` seconds — only 0.5–2 seconds per file.
But the subprocess isolation approach (`index_file_isolated()`) spawns a new Python
process for each file, and Python interpreter startup alone takes ~0.5–1 second.
This left essentially zero time for actual indexing.

When indexing times out for most files, `AnalysisService` returns an empty or
near-empty analysis result. The agent then fails to find any sink candidates
through automatic analysis, and the combination of missing analysis data +
exploration-only mode triggers `unrecoverable_error`.

## Evidence

V10 run `0702-v10-exp-vague-100-0702-1323` on 146 server:
- 39/100 tasks show `unrecoverable_error` in run.log after step 2
- `index_timeout` reasons in analysis service output
- V9 run (same code but before the timeout regression) had 0 `unrecoverable_error`

The regression was introduced when `analysis/service.py` was modified (by user/linter)
to add `RiskSignal`, `SinkDetector`, parallel indexing, and other features. During
that refactor, the timeout formula was changed from a reasonable value to the
too-short `max(0.5, min(2.0, remaining))`.

## Fix Applied

In `analysis/service.py`, `index_repository()` method:

```python
# Before (broken):
per_file_timeout = max(0.5, min(2.0, remaining))

# After (fixed):
per_file_timeout = max(5.0, min(30.0, remaining))
```

Also increased overall timeouts:
- `analysis_timeout_seconds`: 30 → 120
- `automatic_timeout_seconds`: 10 → 60

These values account for:
- Python subprocess startup: ~0.5–1s
- Large C/C++ files with many functions: up to 10–20s for full analysis
- Safety margin for concurrent load on shared server

## Deployment Status

Fix applied locally but **NOT yet deployed** to 146 server. The V10 batch
currently running on remote still has the buggy short timeouts and will
produce ~39/100 failures.

## Lessons

1. When using subprocess isolation, timeouts must account for process startup
   overhead. A minimum of 5 seconds per file is reasonable; 0.5 seconds is
   guaranteed to fail.
2. Changes to `analysis/service.py` should be validated with at least a small
   exploration-only test before launching a 100-task batch.
3. The `per_file_timeout` formula should have a clear lower bound that
   accounts for subprocess overhead, not just "remaining time / N files".

## Priority

**Critical** — Without this fix, any batch run using the current `analysis/service.py`
will have ~40% task failure rate. The fix is a one-line change plus timeout
parameter adjustments.
