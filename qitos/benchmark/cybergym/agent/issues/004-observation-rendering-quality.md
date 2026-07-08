# Observation Rendering Quality — What the Agent Sees

Audit of the observation/prompt rendering pipeline, 2026-06-27.
Level 1 context: no patch.diff available; CVE description is the primary signal.

---

## P20: Vulnerability description truncated to 260 characters

**Severity:** Critical — the single most important piece of Level 1 context is gutted

**Location:** `observations.py:75-76`

```python
desc = state.vulnerability_description.replace("\n", " ")[:260]
```

**Evidence:**

CVE descriptions typically run 500–2000 characters and contain:
- Affected component and version range
- Specific trigger conditions ("when processing EXIF data with IFD tag count > 0")
- Root cause ("heap-buffer-overflow in `format_bytes[tag.format]` access")
- Attack vector details ("via a crafted SFW file")

Truncating to 260 chars frequently loses the trigger condition, root cause, and attack vector — exactly what the agent needs most. For arvo:17986, the description mentions the SFW fuzzer harness and EXIF parsing, but the truncation may cut off the SFW harness hint.

**In Level 1, the CVE description is the ONLY a priori signal about the vulnerability.** There is no patch.diff to infer from. Truncating it is catastrophic.

**Fix:** Render the full description in the Task Context section. If token budget is a concern, show the first 260 chars inline and put the full text in a collapsible/referenced section. Better: prioritize description completeness over less critical sections (repo index, read coverage).

---

## P21: `_build_objective()` ignores all task-specific information

**Severity:** High — the "Task Goal" prompt slot carries zero useful information

**Location:** `adapter.py:227-240`

```python
def _build_objective(self, description, readme, error_txt, patch_diff):
    # All four arguments ignored
    return "Generate the exploit PoC using the files in this repository..."
```

The observation renders a "Task Goal" section from `state.task`, which is set to this generic string. Since it matches the default, `_is_default_task_objective` suppresses it entirely (observations.py:422-428). So the agent has **no task-level goal** in its prompt.

Meanwhile, the actual vulnerability description is only in the "Task Context" section, truncated to 260 chars.

**Fix:** `_build_objective()` should synthesize a task-specific goal from the description. At minimum: "Generate a PoC that triggers [vulnerability_class] in [affected_component] as described in the vulnerability report." This gives the agent a concrete target in the Task Goal slot.

---

## P22: Working memory caps at 6 facts — agent forgets prior discoveries

**Severity:** High — critical code facts are lost during a 30-step run

**Location:** `observations.py:227-256`

```python
code_facts = state.durable_code_facts[-6:]   # last 6
feedback_facts = state.durable_feedback_facts[-6:]  # last 6
```

During a 30-step run, the agent may read 15+ code regions and discover many facts about:
- Function signatures and call chains
- Data structure layouts (struct fields, sizes)
- Branch conditions (parser gates)
- Buffer sizes and allocation patterns

Only the last 6 code facts survive in the observation. Earlier facts (e.g., the harness entry function signature discovered in step 3) may be dropped by step 10, just when they're needed for PoC construction.

Meanwhile, the context compaction system (`CyberGymContextHistory`) replaces old tool results with 320-char previews (160 head + 160 tail), so the original detailed READ output is also lost.

**Result:** The agent has two independent lossy channels (compaction + working memory cap), and neither preserves the information the other drops. Critical facts discovered early are systematically erased.

**Fix:**
1. Raise the working memory cap to 12-15 for code facts (the token cost is small — each fact is a short string).
2. Implement priority-based retention: facts about entry functions, path constraints, and data structures should be harder to evict than facts about unrelated code.
3. Alternatively, merge `durable_code_facts` with `path_constraints` — facts about parser gates should be structural, not textual.

---

## P23: Strategy memory aggressively truncated — feedback details lost

**Severity:** Medium — the agent cannot learn from its own history

**Location:** `observations.py:401-407`

```python
result = (item.get("observed_result") or "")[:80]
feedback = (item.get("stable_feedback") or "")[:110]
next_hypothesis = (item.get("next_hypothesis") or "")[:110]
```

The attempt history shows at most 12 attempts, grouped by strategy family, with each field truncated to 80-110 chars. This means:
- `observed_result` = "no_crash, path_not_reached" (fine, fits)
- `stable_feedback` = the failed gate classification + repair hint (often >110 chars)
- `next_hypothesis` = the agent's planned fix (often >110 chars)

After 6+ attempts with different strategies, the agent needs to see *why* each failed, not just "no_crash". The truncation removes the diagnostic detail that distinguishes one failure from another.

**Fix:** Increase truncation limits to 150-200 chars, or render the full `failed_gate` classification separately from the free-text feedback.

---

## P24: Hot feedback (raw server output) only in initial brief

**Severity:** Medium — agent loses access to the most informative feedback after step 1

**Location:** `observations.py:109-147` (initial brief) vs `observations.py:149-174` (observation packet)

The `_build_initial_brief()` includes a `## Latest Hot Feedback` section showing the last 4 raw feedback records. The `_build_observation_packet()` (used for steps 2+) omits this section entirely.

After step 1, the agent cannot see the raw server output from submissions. It only sees the structured observation (gate classification, crash type, repair hint). This loses:
- The exact ASAN stack trace
- The fuzzer's stdout/stderr
- The verification server's full response

These are critical for diagnosing why a PoC failed. The structured classification is useful but not a substitute for the raw output.

**Fix:** Include at least the last 1-2 hot feedback records in every observation packet, not just the initial brief. The token cost is modest (~500 chars per record).

---

## P25: Constraint board shows only 4 open constraints out of 20 tracked

**Severity:** Medium — agent has blind spots about its own knowledge state

**Location:** `observations.py` (`_constraint_board_lines()`)

The constraint board renders:
- Last 4 harness signals
- Count of confirmed vs open constraints
- Up to 4 open constraint items

With 20 constraints tracked but only 4 displayed, earlier constraints (including the most important ones discovered first, like the harness entry mapping) may be invisible. The agent sees "6 confirmed, 14 open" but cannot see *which* constraints are open without reading state directly.

**Fix:** Show all open constraints (or at least 8-10), sorted by recency or importance. The token cost per constraint is ~1 line.

---

## P26: No constraint status promotion mechanism — constraints never become "confirmed"

**Severity:** High — the constraint model is write-only; the agent can never mark progress

**Evidence:**

In `tools.py`, `_constraint_candidates()` creates `PathConstraint` entries with `status="hypothesized"` or `status="unknown"`. No code anywhere in the codebase updates this status to `"confirmed"`. The `_constraint_board_lines()` display differentiates confirmed vs open, but nothing ever transitions between them.

This means:
1. The "X confirmed, Y open" display in the constraint board is misleading — confirmed count is always 0 (or whatever was seeded).
2. The proposed constraint-aware budget (from issue 002) cannot work because it checks `_has_open_constraints()` — but ALL constraints are permanently open.
3. The agent cannot track its own progress toward understanding the path.

**Fix:** Add a constraint confirmation mechanism:
1. When the agent READs code at a constraint's `source_location` and sees the condition, promote to `confirmed`.
2. When a PoC triggers a crash at a location consistent with a constraint, promote to `confirmed`.
3. When the agent explicitly records evidence for a constraint via `record_hypothesis`, promote to `confirmed`.

---

## P27: Phase guidance is extremely terse (2-7 lines)

**Severity:** Medium — the LLM gets almost no structured methodology per phase

**Location:** `agent_prompts/phase/*.md`

Example: `investigation.md` is approximately:
> "After a few focused searches, switch to READ. Converge on one concrete trigger hypothesis."

This is too vague for Level 1, where the agent must:
1. Find the harness entry function
2. Trace input buffer from entry to parser
3. Identify each parser gate along the path
4. Confirm the bad-state predicate at the sink

The current guidance provides no structure for this workflow. The security expert methodology (issue 003) defines a clear ordered procedure, but none of it is reflected in the phase prompts.

**Fix:** Expand phase guidance files with structured checklists per phase. For investigation:
```
1. Find the harness entry (LLVMFuzzerTestOneInput or main)
2. READ the entry function — trace how input buffer/size are consumed
3. Identify the first parser gate (format check, magic bytes)
4. Follow the call chain toward the vulnerable function
5. For each branch, record the condition as a path constraint
6. Do NOT transition to formulation until at least one path constraint is confirmed
```

---

## P28: Repo index capped at 1800 chars — loses structure of large repos

**Severity:** Low-Medium — for large repos, the agent cannot navigate effectively

**Location:** `repo_analysis.py:61`

The repo index is capped at 1800 chars with at most 12 top-level entries, 8 largest directories, and 15 "interesting" paths. For GraphicsMagick (1000+ source files), this loses the ability to see the `coders/` directory structure, which is critical for understanding which format parsers exist.

**Fix:** Increase the cap to 3000-4000 chars, or make it dynamic based on repo size. Prioritize paths that match the affected component (e.g., if bug is in "EXIF parsing", surface `magick/attribute.c` and `coders/` more prominently).

---

## Summary

| ID | Severity | Issue | Token Impact of Fix |
|----|----------|-------|---------------------|
| P20 | Critical | Description truncated to 260 chars | +500-1500 tokens |
| P21 | High | Objective ignores task info | +50 tokens |
| P22 | High | Working memory cap at 6 facts | +200-400 tokens |
| P23 | Medium | Strategy memory truncated | +100-200 tokens |
| P24 | Medium | Hot feedback only in initial brief | +500-1000 tokens |
| P25 | Medium | Only 4 constraints shown | +100-300 tokens |
| P26 | High | Constraints never confirmed | 0 tokens (logic only) |
| P27 | Medium | Phase guidance too terse | +200-400 tokens |
| P28 | Low-Medium | Repo index cap too low | +200-400 tokens |

**Total estimated token increase: ~2000-5000 tokens** — a modest cost for significant quality improvement.
