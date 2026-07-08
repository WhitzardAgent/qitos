# Tool & Action Interface Quality — How the Agent Acts and Gets Results

Audit of the tool/action pipeline, 2026-06-27.

---

## P30: `_capture_search_signals` only called for GREP — FindSymbols/CallsiteSearch results produce no constraints

**Severity:** High — two powerful tools silently produce no structural state updates

**Location:** `agent.py:893` — the GREP branch calls `_capture_search_signals()`, but FindSymbols and CallsiteSearch branches do not.

**Evidence:**

FindSymbols returns structured match data with `match_id`, `kind`, `preview` — the same enrichment pipeline as GREP. It can find harness functions, parser entry points, and call targets. But because `_capture_search_signals()` is never called on its results:
- No `HarnessSignal` entries are created from FindSymbols results
- No `PathConstraint` entries are created from FindSymbols results
- The agent can find `LLVMFuzzerTestOneInput` via FindSymbols but it won't appear in the Constraint Board's "Harness Signals" section

Similarly, CallsiteSearch finds call relationships (who calls the vulnerable function) — critical for tracing entry→sink — but its results don't populate `path_constraints`.

**Fix:** Call `_capture_search_signals(state, output)` for FindSymbols and CallsiteSearch results as well. The method only reads `output["harness_signal_candidates"]` and `output["constraint_candidates"]`, which are populated by `_enrich_search_matches()` — this method is already called for all three tools.

---

## P31: Signal extraction requires `output_mode="content"` — default mode produces zero constraints

**Severity:** High — the agent's most common GREP usage produces no structural state updates

**Location:** `tools.py:1068-1091` — `_harness_signal_candidates()` and `_constraint_candidates()` operate on `output["matches"]`, which is only populated when `output_mode="content"`.

**Evidence:**

The default GREP output mode is `files_with_matches` (returns matching file paths only). In this mode, `output["matches"]` is an empty list, so:
- `_harness_signal_candidates()` returns nothing
- `_constraint_candidates()` returns nothing
- `_capture_search_signals()` appends nothing to state

The agent must explicitly use `output_mode="content"` to trigger signal capture. But the tool description and phase guidance don't emphasize this. The LLM may reasonably use `files_with_matches` first (to discover which files are relevant), then never follow up with a content-mode GREP on the same files.

**Fix options:**
1. **Always run content-mode internally**: After `files_with_matches` GREP, automatically run a secondary content-mode GREP on the top matches to capture signals. This doubles the cost but ensures constraint capture.
2. **Capture signals from file-level matches**: Extract signals from the file path and match count even without content. For example, if `grep -l "LLVMFuzzerTestOneInput"` returns `fuzz/coder_fuzzer.c`, create a `HarnessSignal` with the file path even without line-level detail.
3. **Default to content mode**: Change the default `output_mode` to `"content"`, which is more useful for a vulnerability-analysis agent anyway.

Option 3 is the simplest and most impactful.

---

## P32: GREP context line cap of 5 is silently enforced

**Severity:** Medium — the LLM may request more context and silently get less

**Location:** `tools.py` — `ctx = max(0, min(int(context or 0), 5))`

The GREP tool silently caps context lines to 5, regardless of what the LLM requests. For code analysis where understanding the surrounding control flow is essential, 5 lines of context may be insufficient — especially for:
- Multi-line conditionals (`if (...) { ... }` spanning 6+ lines)
- Switch statements
- Function definitions with long signatures

The LLM has no way to know its request was silently truncated.

**Fix:** Either raise the cap to 8-10, or add a note in the output when truncation occurs: `// context limited to 5 lines per side`.

---

## P33: `blocking_question` and `runtime_context` parameters pollute 10+ tool schemas

**Severity:** Medium — adds noise to every tool's parameter list, confusing the LLM

**Location:** `tools.py` — 10 of 12 tools include `blocking_question` with the same boilerplate text; all tools include `runtime_context` documented as "Runtime state provided by the engine."

**Evidence:**

The `blocking_question` parameter appears on READ, GREP, GLOB, FindSymbols, CallsiteSearch, RepoMap, FileInfo, HexView, StructProbe, CorpusInspect. Its docstring:
> "Required only in candidate_required mode when you must justify why you need to read instead of constructing a PoC."

This is a control-flow parameter, not a semantic input. The LLM must decide when to fill it and what to say — adding cognitive load to every read-action decision. Meanwhile, `runtime_context` is never filled by the LLM at all (it's injected by the engine), but its presence in the schema takes up attention.

**Fix:**
1. Remove `runtime_context` from the tool schema entirely — it's a framework artifact, not an LLM input.
2. Make `blocking_question` appear only when `candidate_required` mode is active (dynamic schema). In normal mode, don't show it at all.

---

## P34: If `runtime_context` is None, all validation guards silently pass

**Severity:** Medium — silent security bypass

**Location:** `validation.py` — `_validate_tool_access()` and `_validate_bash_command()` check `runtime_context.get("state")` but return empty string (allowing access) if it's None.

If `runtime_context` is not passed correctly (framework bug, race condition, etc.), all tool access guards silently fail open. The agent can read indefinitely even under budget exhaustion, bypass BASH restrictions, etc.

**Fix:** If `runtime_context` is None or missing the `state` key, return a blocking message instead of an allow-through. Fail closed, not open.

---

## P35: Snip compaction reduces tool results to 320 chars — critical detail lost

**Severity:** Medium — old READ results become nearly useless after compaction

**Location:** `context.py` — Level 1 compaction replaces tool results with 160 head + 160 tail chars.

For a READ result showing a 200-line function, the compaction keeps:
- First 160 chars (maybe the function signature and first few lines)
- Last 160 chars (maybe the closing brace and a few trailing lines)
- Everything in between is lost

The agent's `durable_code_facts` mechanism is supposed to preserve key facts across compaction, but:
1. `_extract_findings_from_read()` only extracts function names, not path constraints or data structure details
2. The 6-fact cap (P22) means many facts are dropped even if captured
3. Facts are unstructured text strings, not the structured `PathConstraint` model

**Fix:**
1. Increase the snip preview to 300 head + 300 tail (600 total) — still compact but preserves more context.
2. Before compaction, extract structural information (path constraints, function signatures, branch conditions) into `durable_code_facts` or `path_constraints`. This is the "compaction-resistant" knowledge store.
3. Prioritize preservation of constraint-relevant content over other content.

---

## P36: No constraint extraction from READ results — only from GREP

**Severity:** High — the agent's primary investigation tool produces no structural constraints

**Location:** `tools.py` — READ results go through `_extract_findings_from_read()` (which only adds function names to `vulnerable_functions`) but NOT through any constraint extraction pipeline.

**Evidence:**

When the agent READs a source file and sees:
```c
if (memcmp(data, "SFW", 3) != 0)
    return 0;  // format rejected
```

This is clearly a **parser gate** — a constraint the input must satisfy. But `_extract_findings_from_read()` only looks for function definitions (regex `^(static\s+)?\w+\s+\*?\s*\w+\s*\(`). It doesn't detect:
- Branch conditions (`if`, `switch`, `case`)
- Comparison operations (`memcmp`, `strcmp`, `==`, `!=`)
- Return-early patterns (guard conditions)
- Magic number checks

The only constraint capture path is GREP with `output_mode="content"` (P31), which requires the agent to explicitly GREP for parser gates. But the agent doesn't know to do this — it just READs code and moves on.

**Fix:** Add `_extract_path_constraints_from_read()` that:
1. Scans READ content for control-flow guards (if/switch/assert statements)
2. For each guard, creates a `PathConstraint` with `status="hypothesized"`
3. Correlates with existing constraints — if the READ location matches a constraint's `source_location`, promote to `confirmed`

This is the READ-side counterpart to P26 (constraint confirmation).

---

## P37: `wrong_trigger` gate classification is overly broad

**Severity:** Low-Medium — lumps together different failure modes

**Location:** `feedback.py:203-241` — `_classify_failed_gate()`

The `wrong_trigger` gate covers:
1. ASAN overflow/UAF detected
2. Generic crash without location info

These are quite different:
- ASAN overflow means the PoC reached the vulnerable code AND triggered memory corruption, but the crash signature doesn't match the expected one (discriminant failure). This is close to success.
- Generic crash without location could be a null dereference, a segfault in unrelated code, or an assertion failure. This is far from success.

The repair hint for both is: "Change the trigger bytes, field values, or state transitions." But for an ASAN overflow, the right fix is "refine the overflow parameters to match the expected crash signature," while for a generic crash, the right fix is "figure out why you're crashing in the wrong place."

**Fix:** Split `wrong_trigger` into:
- `trigger_wrong_signature`: ASAN/crash detected at the right location but wrong type — close to success, refine trigger
- `trigger_wrong_location`: crash in unexpected location — still far from the target, reconsider path

---

## Summary

| ID | Severity | Issue | Type |
|----|----------|-------|------|
| P30 | High | FindSymbols/CallsiteSearch produce no constraints | Signal capture gap |
| P31 | High | GREP default mode produces zero constraints | Signal capture gap |
| P32 | Medium | GREP context cap silently enforced | Tool quality |
| P33 | Medium | `blocking_question`/`runtime_context` pollute schemas | Schema noise |
| P34 | Medium | Validation guards fail open when runtime_context is None | Safety defect |
| P35 | Medium | Compaction reduces tool results to 320 chars | Context preservation |
| P36 | High | No constraint extraction from READ results | Signal capture gap |
| P37 | Low-Medium | `wrong_trigger` gate too broad | Classification quality |
