# Phase Engine, State & Feedback Quality — Decision-Making Infrastructure

Audit of the agent's decision-making infrastructure, 2026-06-27.
Level 1 context: no patch.diff, CVE description is the only a priori signal.

---

## P40: Ingestion→Investigation transition is effectively unconditional

**Severity:** High — the phase engine provides no structural gate for the first transition

**Location:** `phase.py`

```python
PhaseSpec(
    name="ingestion",
    transitions=[
        TransitionRule(target="investigation",
            condition=lambda s: bool(s.vulnerability_description),
            priority=10),
        TransitionRule(target="investigation",
            condition=lambda s: _phase_local_steps(s) >= 2,
            priority=0),
    ],
    max_steps=2,
)
```

`vulnerability_description` is always set at init (falls back to the raw task string), so the first rule always fires on step 1. The agent never actually "ingests" anything — it transitions immediately.

**Why this matters for Level 1:** In Level 1, the agent needs to deeply understand the CVE description before starting investigation. The description contains:
- The vulnerability class
- The affected component
- Specific trigger conditions
- References to code constructs

But the phase engine skips ingestion entirely, so the agent never has a structured "understand the task" phase. It goes straight to code-level investigation with only a 260-char truncated description.

**Fix:** The ingestion phase should require:
1. The vulnerability description has been read in full
2. At least one specific code artifact has been identified from the description (not just the generic task string)
3. The bug type has been classified

Or: repurpose ingestion as a "description analysis" phase where the agent extracts structured information from the CVE description before touching code.

---

## P41: Investigation→Formulation transitions on `vulnerable_functions` alone — reachability ≠ readiness

**Severity:** High — the agent formulates PoCs before understanding the path

**Location:** `phase.py`

```python
TransitionRule(target="formulation",
    condition=lambda s: bool(s.trigger_hypothesis or s.vulnerable_functions or s.vulnerable_files),
    priority=10)
```

`vulnerable_functions` is populated by `_extract_affected_component()` from the CVE description — no source reading required. This means the agent can transition to formulation after a single GREP that confirms the function exists, without understanding:
- How input reaches the function (entry mapping)
- What constraints the input must satisfy (parser gates)
- What the bad-state predicate is (trigger condition)

The 7-step fallback forces transition regardless, but even 7 steps is often insufficient for complex entry→sink paths.

**This is the structural cause of the `path_not_reached` loop.** The agent is pushed to formulate before it has collected the constraints needed for a viable PoC.

**Fix:** Add a constraint-coverage requirement to the transition:
- At least N constraints must be in `confirmed` status (requires P26 — constraint confirmation mechanism)
- OR at least one trigger constraint (not just reachability) must be identified
- The 7-step fallback should be conditional on constraint progress (if new constraints were discovered in the last 3 steps, extend investigation)

---

## P42: Read budget of 8 is too aggressive for Level 1

**Severity:** High — combined with premature formulation, leaves the agent blind

**Location:** `constants.py:27` — `NO_CANDIDATE_READ_ACTION_LIMIT = 8`

In Level 1, the agent has no patch.diff and must discover everything from source code reading. Understanding an entry→sink path typically requires:
1. Read harness entry function (1 READ)
2. Read format decoder (1 READ)
3. Read parser entry (1 READ)
4. Read specific branch conditions (2-3 READs)
5. Read vulnerable function context (1 READ)
6. Read data structure definitions (1-2 READs)

That's 7-9 READs just for the path, before any constraint validation or PoC construction. With 8 as the limit, the agent has almost no headroom for iterative deepening after formulation.

The reinvestigation escape valve at 12 failed submits is too far away — by then, 12+ steps have been wasted on blind PoC generation.

**Fix:**
1. Raise the base limit to 12-15 for Level 1 (or make it configurable per task complexity).
2. Lower the reinvestigation threshold from 12 to 6 failed submits.
3. Reset the read counter when reinvestigation is triggered (currently, the counter is not reset, so the agent gets a few reads at most before being blocked again).

---

## P43: `post_submit_miss` mode is short-lived — feedback guidance gets one turn

**Severity:** Medium — the agent sees gate-specific guidance for at most one step

**Location:** `validation.py:167-180` — control mode priority

```python
if state.pending_reflection:
    return "reflection_pending"
if ValidationMixin._ready_poc_paths(state):
    return "candidate_ready"  # <-- supersedes post_submit_miss
if state.last_verification_result and not state.is_verified():
    return "post_submit_miss"
```

After a failed submit, the control mode becomes `post_submit_miss`, which:
- Shows gate-specific repair guidance in the prompt
- Suggests specific next actions (READ the parser entry, modify a field)

But as soon as the agent writes a new PoC file (even in the same step), `ready_poc_paths()` becomes non-empty, and the control mode switches to `candidate_ready`. The gate-specific guidance disappears, replaced by "submit now" mode.

In practice, the agent may:
1. See `path_not_reached` → `post_submit_miss` mode → "READ the parser entry"
2. Write a modified PoC instead → `candidate_ready` mode → "submit now"
3. Submit → `path_not_reached` again → `post_submit_miss` → same guidance
4. Repeat

The `post_submit_miss` guidance never has time to influence the agent's investigation because the mode switches too quickly.

**Fix:**
1. Add a cooldown: after `post_submit_miss`, keep the mode for at least 2 steps unless the agent explicitly requests to proceed.
2. Or: merge the gate-specific guidance into the `candidate_ready` mode — even when a PoC is ready, remind the agent *why* the previous one failed.
3. Or: require the agent to explicitly acknowledge the feedback before creating a new candidate (via `record_hypothesis` or similar).

---

## P44: Reflection mechanism is LLM-driven with no structural analysis

**Severity:** Medium — the agent reflects in free text, not in structured constraint terms

**Location:** `feedback.py` — `_update_failure_counters()`, `record_reflection` tool

The current reflection mechanism:
1. After 5 identical failure signatures, forces `reflection_pending` mode
2. The agent must call `record_reflection(summary, next_step)`
3. The summary is free text — the LLM decides what to reflect on
4. No structural analysis is injected into the reflection prompt

This means the reflection quality depends entirely on the LLM's self-awareness. Common failure patterns:
- The agent reflects on surface symptoms ("my PoCs keep failing") rather than root causes ("I haven't verified the format gate")
- The agent proposes the same strategy with minor variations instead of fundamentally reconsidering
- The agent doesn't check which constraints are still open (because it can't — see P26)

**Fix:** Inject a structural constraint audit into the reflection prompt:
```
Constraint audit: 0/7 constraints confirmed, 5 hypothesized, 2 unknown
Open constraints:
  - [hypothesized] SFW magic bytes must be valid (at coders/sfw.c:245)
  - [unknown] How input reaches GenerateEXIFAttribute
  - ...
You have attempted 5 PoCs targeting GenerateEXIFAttribute without verifying the entry path.
Consider: before generating another PoC, READ the harness entry to trace how input reaches the vulnerable function.
```

---

## P45: No pre-submission sanity check — implausible files waste submit attempts

**Severity:** Medium — the agent can submit clearly broken files

**Location:** `validation.py` — `_candidate_file_exists()` only checks file existence

The agent can submit:
- A Python script instead of a binary input
- An empty file
- A file that doesn't match the expected format at all (e.g., a JPEG when the harness expects SFW)
- A file larger than typical fuzzer inputs (10MB+)

Each wastes a submit attempt and returns `path_not_reached` or `submission_error`, providing minimal diagnostic value.

**Fix:** Add lightweight pre-submission checks:
1. File non-empty (size > 0)
2. File size reasonable (< 10MB for fuzzer inputs)
3. If `input_format` has magic bytes defined, check that the file's first bytes match
4. If the PoC was generated by a Python script, check the script's exit code was 0

These checks don't require source reading — they're cheap and can catch the most obvious wastes.

---

## P46: Bug type detection is fragile substring matching — many CVEs get no guidance

**Severity:** Medium — 8 hardcoded patterns miss many vulnerability classes

**Location:** `task_analysis.py:17-53`

The `_classify_bug_type()` function matches 8 patterns:
- "buffer overflow", "heap overflow", "stack overflow"
- "use-after-free", "double free"
- "null pointer dereference", "null dereference"
- "integer overflow", "signedness"
- "format string"
- "command injection", "code injection"
- "race condition"
- "out of bounds"

Missing classes that are common in real CVEs:
- Type confusion
- Use of uninitialized value
- Information disclosure / memory leak
- Denial of service (infinite loop, resource exhaustion)
- Privilege escalation
- Logic bugs / incorrect calculation
- TOCTOU (time-of-check-time-of-use)
- XSS / injection variants beyond command/code

When the bug type is empty, no bug-type-specific guidance is rendered, and the agent has no domain-specific strategy.

**Fix:**
1. Add more bug type patterns.
2. Add a fallback: if no pattern matches, classify as "memory_corruption" if ASAN/UBSAN keywords are present, "logic_bug" if description mentions calculation/comparison/error, or "unknown" with generic guidance.
3. Consider using LLM-based classification for descriptions that don't match any pattern.

---

## P47: Entrypoint detection only matches `parse/read/decode` — misses common patterns

**Severity:** Medium — the agent doesn't discover entry functions with other naming patterns

**Location:** `task_spec.py:121-122`

```python
likely_entrypoints = [s for s in symbol_mentions
                      if s.startswith(("parse", "read", "decode"))]
```

Common entry function prefixes not covered:
- `handle_`, `process_`, `accept_`, `consume_`
- `on_`, `do_`, `execute_`
- `transform_`, `convert_`, `render_`
- `encode_`, `serialize_`, `deserialize_`
- `load_`, `import_`, `open_`
- Fuzzer-specific: `LLVMFuzzerTestOneInput`, `*_fuzzer`

**Fix:** Expand the prefix list to at least:
```python
("parse", "read", "decode", "handle", "process", "accept",
 "consume", "transform", "convert", "load", "import",
 "LLVMFuzzerTestOneInput")
```

Or: use a heuristic — any function name that appears in the CVE description is a likely entrypoint, regardless of prefix.

---

## P48: No mechanism to trace input buffer from harness entry to parser-visible pointer

**Severity:** Critical — the most fundamental security analysis step is entirely absent

**Location:** This is an architectural gap — no code anywhere traces the input mapping.

The security expert methodology (issue 003) states this must be the **first** thing done:
> "Identify the official harness/entry in source code; from the entry function start, trace raw artifact/input buffer and size through every source-level consumption/transformation until the target library/parser call; record the parser-visible pointer/slice/length."

Our agent never does this. It:
1. Finds the vulnerable function name from the CVE description
2. Reads some code around the function
3. Starts constructing PoCs

Without the input mapping, the agent cannot know:
- What offset in the PoC file corresponds to what the parser sees
- Whether the harness consumes prefix bytes (FUZZ_seed, FuzzedDataProvider)
- How many transformations happen between file bytes and parser input
- Whether the parser gets the full file or a substring/slice

This is the root cause of most `path_not_reached` failures: the agent constructs PoCs that would trigger the vulnerability IF the bytes went directly to the parser, but they don't because the harness transforms the input first.

**Fix:** This requires a new workflow step or tool:
1. After finding the harness entry, READ the entry function and trace how `data`/`size` flow through the code.
2. Record the transformation chain as a structured field in state: `input_mapping: List[MappingStep]` where each step is `{operation, consumed_bytes, remaining_offset}`.
3. Block PoC construction until `input_mapping` is non-empty (or at least warn that construction is speculative without it).
4. Show the input mapping in the observation: "Input bytes 0-N are consumed by [operation], parser sees bytes N+1 onwards."

---

## P49: `plan` and `plan_cursor` fields in state are unused — no planning mechanism exists

**Severity:** Low — but the absence of planning is a structural gap

**Location:** `state.py` — `plan: str = ""`, `plan_cursor: int = 0`

These fields exist in `CyberGymState` but are never substantively used by the agent loop. `reduce()` never reads or writes them. The agent has no mechanism to:
1. Create a step-by-step plan for reaching the vulnerability
2. Track progress against the plan
3. Adjust the plan based on new findings

Instead, the agent operates reactively: observe → think → act, with no long-horizon strategy. This works for simple tasks but breaks down for complex entry→sink paths that require ordered multi-step investigation.

**Fix:** Either:
1. Remove these fields (they're dead weight and misleading)
2. Or implement a lightweight planning mechanism where the agent records its investigation plan in `plan` and the reduce loop checks `plan_cursor` progress

For Level 1, option 2 could be valuable: the agent plans "1) find harness, 2) trace input mapping, 3) identify parser gates, 4) confirm trigger condition" and the plan serves as a structured checklist. But this is a larger feature and may not be worth the complexity.

---

## Summary

| ID | Severity | Issue | Category |
|----|----------|-------|----------|
| P40 | High | Ingestion is a no-op phase | Phase engine |
| P41 | High | Investigation→Formulation on reachability alone | Phase engine |
| P42 | High | Read budget of 8 is too aggressive for Level 1 | Budget |
| P43 | Medium | `post_submit_miss` guidance lasts one step | Feedback |
| P44 | Medium | Reflection is free-text, no structural audit | Feedback |
| P45 | Medium | No pre-submission sanity check | Validation |
| P46 | Medium | Bug type detection has 8 hardcoded patterns | Task analysis |
| P47 | Medium | Entrypoint detection misses common prefixes | Task analysis |
| P48 | Critical | No input mapping trace (entry→parser) | Architectural gap |
| P49 | Low | `plan`/`plan_cursor` unused | State dead weight |

**The three most impactful fixes:** P48 (input mapping), P41 (phase gating), P42 (read budget). These address the root cause of the `path_not_reached` loop: the agent doesn't understand how input reaches the vulnerable code, it's pushed to formulate too early, and it doesn't have enough reads to understand the path even if it tried.
