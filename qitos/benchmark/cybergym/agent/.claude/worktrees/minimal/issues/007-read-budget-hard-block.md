# Read Budget Hard Block — Exploration Forced to Stop

Analysis of the read budget mechanism and its destructive interaction with
Level 1 (no patch.diff) tasks, 2026-06-27.

---

## Problem Statement

The agent's read budget **hard-blocks** READ/GREP/BASH-search after
`NO_CANDIDATE_READ_ACTION_LIMIT` (currently 12) read actions in the
formulation/verification phase. When the budget is exhausted, the agent
*cannot read source code at all* — it can only create PoC files and submit them.

This is the single biggest cause of the `path_not_reached` loop: the agent
is forced to submit blind guesses without understanding the entry→sink path,
gets zero-signal feedback, and is still blocked from reading to diagnose
the failure.

---

## Current Mechanism (Full Trace)

### 1. Budget counter: `_track_read_budget()` (validation.py:648-684)

```python
if short_name not in readish and normalized_name not in readish:
    return
if state.current_phase not in ("formulation", "verification"):
    return                     # ← investigation phase: UNLIMITED
state.phase_read_actions += 1  # ← formulation/verification: COUNTED
```

Every READ, GREP, GLOB, FindSymbols, CallsiteSearch, RepoMap, CorpusInspect,
FileInfo, HexView, StructProbe call in formulation/verification increments
`phase_read_actions`. Investigation phase is unlimited.

### 2. Budget check: `_read_budget_exhausted()` (validation.py:109-114)

```python
def _read_budget_exhausted(state) -> bool:
    return (
        state.current_phase in ("formulation", "verification")
        and not ValidationMixin._ready_poc_paths(state)
        and state.phase_read_actions >= NO_CANDIDATE_READ_ACTION_LIMIT  # 12
    )
```

### 3. Three hard-block enforcement points

**Point A — Tool access validation** (validation.py:355-361):

```python
if (FORCE_SUBMIT_HARD
    and self._read_budget_exhausted(state)
    and not self._should_reinvestigate(state)
    and not self._constraint_reinvestigation_allowed(state)):
    return READ_BUDGET_HARD_BLOCK_TEXT  # ← BLOCKS READ/GREP/evidence tools
```

**Point B — BASH search/browse blocking** (validation.py:512-525):

Same condition blocks BASH when it's used for searching/browsing source code.

**Point C — Control mode switch** (validation.py:191-192):

```python
if ValidationMixin._read_budget_exhausted(state):
    return "candidate_required"  # ← changes tool schema, objective, prompt
```

When `candidate_required` mode is active:
- `_allowed_tool_lines()` only shows construction/submit tools
- `_current_objective()` says "Prioritize forming a concrete PoC"
- The prompt de-emphasizes investigation entirely

### 4. Two escape valves (both inadequate)

**Valve 1 — `_should_reinvestigate()`** (validation.py:117-128):

```python
return attempts >= REINVESTIGATE_AFTER_SUBMITS and best <= 0
# REINVESTIGATE_AFTER_SUBMITS = 6
```

Requires 6 failed submissions with score 0. In the latest run, the agent
only managed 2 submissions in 17 steps (budget_time killed it first).

**Valve 2 — `_constraint_reinvestigation_allowed()`** (validation.py:131-144):

```python
for item in state.path_constraints:
    if item.status in {"unknown", "hypothesized", "open"}:
        return True
```

Allows reads if ANY constraint is not confirmed. But:
- The constraint confirmation mechanism (P26) was never implemented — constraints
  are never promoted to `confirmed`. So this valve is always open.
- However, it only allows reads through `_candidate_targeted_read_allowed()`,
  which is capped at `ACTIVE_CANDIDATE_TARGETED_READ_LIMIT = 2`.
- The `reads_left=2` in the trace comes from this cap, not the main budget.

### 5. Phase transition resets the counter (agent.py:768)

```python
if new_phase != old_phase:
    state.phase_read_actions = 0  # ← RESET on phase transition
```

When the agent transitions from verification→formulation after a failed
submit, `phase_read_actions` resets to 0. But it's still in formulation,
so reads are counted again and the budget refills slowly.

---

## What Happened in the Latest Run (arvo:17986)

```
Step 0: ingestion       → orienting          reads_left=2
Step 1: investigation   → no_candidate       reads_left=2
Step 2: formulation     → no_candidate       reads_left=2   ← premature transition!
Step 3-5: formulation   → no_candidate       reads_left=2
Step 6-8: formulation   → candidate_required reads_left=2   ← READ BLOCKED
Step 9-16: formulation  → post_submit_miss   reads_left=2   ← 2 submissions, both failed
STOP: budget_time (17 steps, didn't reach 30)
```

**Key observations:**

1. **Investigation only lasted 1 step** (step 1). The agent found
   `vulnerable_functions` from the CVE description and immediately
   transitioned to formulation. The P41 constraint-gate is ineffective
   because `trigger_hypothesis` is also set from the description, and
   the OR condition `or bool(s.trigger_hypothesis)` bypasses the
   constraint check.

2. **Reads in investigation are unlimited** but the agent only spent 1
   step there. Once in formulation, the clock starts.

3. **`reads_left=2` throughout** — this is the
   `ACTIVE_CANDIDATE_TARGETED_READ_LIMIT = 2`, not the main budget.
   The `_constraint_reinvestigation_allowed` valve is open (constraints
   are never confirmed), so the hard block doesn't fire. But the
   targeted-read limit of 2 is still very restrictive.

4. **Only 2 submissions in 17 steps** — the agent spent most steps
   constructing PoC files with BASH/WRITE, not reading or submitting.
   The `candidate_required` mode forced it to focus on construction,
   but the PoCs were blind guesses (JPG format instead of SFW).

---

## Root Cause Analysis

The budget mechanism has **three fundamental design flaws**:

### Flaw 1: Budget assumes "more reading = procrastination"

The design philosophy is: if the agent hasn't produced a candidate after N
reads, it's procrastinating, so force it to submit. This is wrong when:

- The agent hasn't collected enough constraints to construct a viable PoC
  (Level 1: no patch.diff, must discover everything from source)
- The feedback signal (`path_not_reached`) is zero-signal — it doesn't tell
  the agent *why* the PoC failed, so forced submission teaches nothing
- The agent is making progress (discovering new path constraints) but
  hasn't yet reached the threshold for a viable PoC

**Security analysis is a constraint satisfaction problem, not a search
problem.** You don't "try PoCs until one works." You collect constraints
until you have enough evidence to construct a PoC that satisfies all of
them, then verify locally, then submit.

### Flaw 2: Budget is per-phase, but the problem is cross-phase

The agent reads in investigation (unlimited), then transitions to
formulation where reads are limited. But the information needed for PoC
construction is discovered across both phases:

- Investigation: find the vulnerable function, identify the call chain
- Formulation: understand the specific path constraints, verify trigger
  conditions, confirm data structure layouts

The formulation phase NEEDS reads just as much as investigation, because
the agent's understanding is incomplete after investigation. The budget
assumes formulation = "write PoC now" but it's really "finish understanding
the path, then write PoC."

### Flaw 3: Escape valves are too conservative

- `_should_reinvestigate()`: requires 6 failed submissions — by then,
  12+ steps have been wasted on blind generation
- `_constraint_reinvestigation_allowed()`: limited to 2 targeted reads —
  insufficient for understanding a complex path
- Both valves only open AFTER damage has been done (failed submits). They
  don't prevent the damage.

---

## Proposed Fix: Soft Guidance Instead of Hard Block

### Principle

Replace the hard READ/GREP block with **soft guidance** that nudges the
agent toward PoC construction but doesn't prevent investigation when the
agent is making progress.

### Changes

#### Change 1: Remove the hard block from tool access validation

**File:** `agent_impl/validation.py`

In `_validate_read_tool_access()` (line 355-361) and `_validate_bash_command()`
(line 512-525), remove the `FORCE_SUBMIT_HARD` hard block:

```python
# BEFORE:
if (FORCE_SUBMIT_HARD
    and self._read_budget_exhausted(state)
    and not self._should_reinvestigate(state)
    and not self._constraint_reinvestigation_allowed(state)):
    return READ_BUDGET_HARD_BLOCK_TEXT

# AFTER:
# No hard block. The control mode switch (candidate_required) and
# objective prompt provide soft guidance. The agent can still READ
# if it determines that reading is necessary for PoC construction.
```

Keep `_derive_control_mode()` switching to `candidate_required` — this
changes the prompt emphasis and tool schema ordering, but doesn't
prevent the agent from calling READ/GREP when it decides to.

#### Change 2: Add a soft budget reminder instead of hard block

**File:** `agent_impl/observations.py`

When `phase_read_actions >= NO_CANDIDATE_READ_ACTION_LIMIT`, add a
reminder in the observation (not a block):

```python
def _one_shot_reminder_lines(self, state):
    lines = []
    reminder = str(getattr(state, "pending_reminder", "") or "").strip()
    if reminder:
        ...
    # Soft budget reminder
    if (state.current_phase in ("formulation", "verification")
        and state.phase_read_actions >= NO_CANDIDATE_READ_ACTION_LIMIT
        and not self._ready_poc_paths(state)):
        lines.append(
            "BUDGET NOTE: You've done many reads without producing a PoC. "
            "Consider whether you now have enough understanding to construct "
            "a candidate. If a specific read is still needed to unblock PoC "
            "construction, proceed with it — but don't read speculatively."
        )
    return lines
```

#### Change 3: Remove FORCE_SUBMIT_HARD constant and all references

**File:** `agent_impl/constants.py`

Remove `FORCE_SUBMIT_HARD` and `READ_BUDGET_HARD_BLOCK_TEXT`.

#### Change 4: Raise targeted-read limit from 2 to 6

**File:** `agent_impl/constants.py`

```python
# BEFORE:
ACTIVE_CANDIDATE_TARGETED_READ_LIMIT = 2

# AFTER:
ACTIVE_CANDIDATE_TARGETED_READ_LIMIT = 6
```

In `candidate_required` mode, the agent can still do targeted reads (up to
6 instead of 2). This gives enough room for constraint-verification reads
after a failed submit.

#### Change 5: Keep `candidate_required` control mode but soften its tool schema

**File:** `agent_impl/observations.py` (`_allowed_tool_lines`)

Currently, `candidate_required` mode only shows construction tools:

```python
if self._should_filter_to_candidate_tools(state):
    names = self._candidate_construction_tool_names(state)
    # Only WRITE, BASH, submit_poc, etc.
```

Change `_candidate_construction_tool_names` to also include READ and GREP:

```python
def _candidate_construction_tool_names(self, state):
    names = {self.WRITE_TOOL, self.BASH_TOOL, self.APPEND_TOOL,
             self.INSERT_TOOL, self.REPLACE_LINES_TOOL, self.STR_REPLACE_TOOL,
             SUBMIT_POC_TOOL}
    # Allow targeted reads for constraint verification
    names.add(self.READ_TOOL)
    names.add(self.GREP_TOOL)
    return names
```

This way, the prompt still emphasizes "make a PoC now" but the agent can
call READ/GREP when it determines it needs to check a specific constraint.

---

## Implementation Checklist

- [ ] Remove `FORCE_SUBMIT_HARD` hard block from `_validate_read_tool_access()`
- [ ] Remove `FORCE_SUBMIT_HARD` hard block from `_validate_bash_command()`
- [ ] Remove `FORCE_SUBMIT_HARD` constant and `READ_BUDGET_HARD_BLOCK_TEXT`
- [ ] Add soft budget reminder in `_one_shot_reminder_lines()`
- [ ] Raise `ACTIVE_CANDIDATE_TARGETED_READ_LIMIT` from 2 to 6
- [ ] Add READ/GREP to `_candidate_construction_tool_names()`
- [ ] Update test `test_path_not_reached_allows_targeted_search_after_read_budget`
      — it currently asserts the hard block fires; change to assert soft reminder

---

## Verification

1. Run agent on arvo:17986 with 30 steps
2. Check: agent should be able to READ in formulation phase after budget
   is "exceeded"
3. Check: the soft reminder appears in the observation
4. Check: `candidate_required` mode still emphasizes PoC construction
5. Check: agent can do targeted reads to verify constraints after
   `path_not_reached` feedback
6. Check: no regression in cases where the agent WAS procrastinating
   (reading unrelated code instead of constructing a PoC)
