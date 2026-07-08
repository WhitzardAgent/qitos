# Exploration Budget & Constraint Collection Gap Analysis

Rooted in observed runs on `arvo:17986` and analysis of current agent architecture, 2026-06-27.

---

## P10: Exploration budget hard-cutoff forces blind PoC generation

**Severity:** High — the single biggest source of wasted steps

**Evidence:**

The current read budget works as follows (`agent_impl/constants.py`):
- `NO_CANDIDATE_READ_ACTION_LIMIT = 8` — after 8 read/search actions in formulation/verification phase, READ/GREP are **hard-blocked**
- The block message: *"Exploration budget exhausted... This step you MUST create a concrete PoC input file"*

In the updated run, this fired at step 28 (observation shows `Exploration budget exhausted`). The agent had only partially understood the vulnerable path:
- It knew `GenerateEXIFAttribute` was the vulnerable function
- It knew the ChangeLog said `coder_SFW_fuzzer`
- It had NOT identified the complete entry→sink path (how SFW input reaches `GetImageProfile("EXIF")` → `GenerateEXIFAttribute` → the specific buffer overflow branch)
- It had NOT collected the path constraints (what fields must be set, what branches must be taken)

**Why this is wrong:**

The budget assumes "more reading = procrastination" and that forcing early submission always yields useful feedback. This is false when:

1. The agent hasn't finished collecting **path constraints** — the conditions that input must satisfy to reach the vulnerable code. Submitting without these is pure guesswork, not "useful feedback."
2. The `no_trigger` / `path_not_reached` feedback is extremely low-signal. It tells the agent "you didn't reach the code" but not *why* or *which constraint was violated*. Each such failure consumes a step and provides near-zero actionable information.
3. After a `path_not_reached` result, the `_feedback_action_guidance` says "READ the parser entry to identify the path-gating condition" — but the budget block prevents this very action. The agent is trapped: feedback says "read more", budget says "stop reading."

**Current mechanism:**

```python
# validation.py:107
def _read_budget_exhausted(state) -> bool:
    return (
        state.current_phase in ("formulation", "verification")
        and not _ready_poc_paths(state)
        and state.phase_read_actions >= 8  # NO_CANDIDATE_READ_ACTION_LIMIT
    )
```

The budget counts only actions in `formulation`/`verification` phases. The `investigation` phase has no limit but auto-transitions to `formulation` after `force_at_step=7`. So effectively:
- Investigation: at most 7 steps of free reading
- Formulation: at most 8 more reads before hard block
- Total exploration budget: ~15 read actions to understand an entire vulnerability

For complex projects like GraphicsMagick (with multiple parsers, format hierarchies, and deep call chains), 15 read actions is grossly insufficient to map entry→sink.

**Proposed fix direction:**

1. Replace the hard cutoff with a **constraint-completeness check**: instead of "8 reads → force submit", use "have we collected the minimum constraints needed to construct a PoC?" If the answer is no, allow more reading.
2. Make the budget **soft** with a cool-down rather than a hard block: after N reads, show a reminder to consider submitting, but don't block reads entirely. Only force-submit after M additional reads with no constraint progress.
3. Reset or extend the budget when feedback returns `path_not_reached` — this signal explicitly says "you need to understand the path better," which requires more reading, not less.

---

## P11: No mechanism to collect or represent entry→sink path constraints

**Severity:** High — the agent has no structured model of what it needs to learn

**Evidence:**

The current state tracks investigation findings through:
- `vulnerable_files: List[str]` — file names
- `vulnerable_functions: List[str]` — function names
- `trigger_hypothesis: str` — free-text guess
- `durable_code_facts: List[str]` — unstructured text facts
- `durable_feedback_facts: List[str]` — unstructured feedback facts
- `evidence_index: Dict[str, Any]` — semi-structured evidence pointers

None of these represent the **path constraint chain** — the sequence of conditions that input data must satisfy to travel from the harness entry point to the vulnerable code location. For example, for the GraphicsMagick case:

```
Entry: LLVMFuzzerTestOneInput(data, size)
  → SFW decoder must recognize format (magic bytes: "SFW" header)
    → SFW→JPEG conversion must succeed
      → JPEG decoder must extract EXIF profile
        → GetImageProfile("EXIF") must return non-null
          → GenerateEXIFAttribute must be called
            → IFD parsing must reach a specific tag
              → format_bytes[tag.format] access overflows (THE BUG)
```

Each arrow is a **constraint**. Missing any one = `path_not_reached`. The agent currently has no structured way to:
1. Know which constraints it has confirmed vs which are still unknown
2. Track progress toward "all constraints confirmed"
3. Prioritize reading to fill the most impactful gap
4. Explain to itself *why* a PoC failed (which constraint was violated)

**What the prompt says:**

The `_current_objective` for investigation phase is:
> "Narrow to one concrete vulnerable path and extract the trigger condition."

And the `path_not_reached` repair hint:
> "READ the parser entry to identify the path-gating condition (which branch must be taken, which field routes input toward the vulnerable function). Then modify the corresponding field in your PoC."

These are good instructions, but they're unstructured text — the agent has no scaffold to organize the constraints it discovers. It relies entirely on the LLM's working memory (context window) to track them, which degrades rapidly as context fills and compaction occurs.

**Proposed fix direction:**

Add a structured `PathConstraint` model to state:

```python
@dataclass
class PathConstraint:
    """A single condition that input must satisfy to reach the vulnerable code."""
    description: str        # e.g., "SFW header magic must be valid"
    source_location: str    # e.g., "coders/sfw.c:245"
    status: str             # "confirmed" | "hypothesized" | "unknown"
    required_values: str    # e.g., "first 3 bytes = 0x53 0x46 0x57"

state.path_constraints: List[PathConstraint] = field(default_factory=list)
```

This would allow:
1. The agent to explicitly track what it knows vs doesn't know
2. The prompt to show constraint completion: "3/7 constraints confirmed"
3. The budget system to gate on constraint completeness rather than raw read count
4. Post-failure analysis to identify which constraint was likely violated

---

## P12: `path_not_reached` feedback is too low-signal for effective iteration

**Severity:** Medium — the most common failure mode provides the least guidance

**Evidence:**

In both runs, all PoC submissions resulted in `path_not_reached`. The current feedback processing (`_classify_failed_gate`) classifies this correctly and provides a generic repair hint:

> "READ the parser entry to identify the path-gating condition"

But the agent doesn't know *which* gate it failed at. Was it:
- The format magic was wrong (carrier_parse failure)?
- The parser rejected the file before reaching EXIF?
- The EXIF profile was present but malformed?
- The IFD parsing took a different branch?

The fuzzer output only shows:
```
/out/coder_JPG_fuzzer: Running 1 inputs 1 time(s) each.
Running: /tmp/poc
Executed /tmp/poc in 5 ms
```

No crash, no ASAN output, no intermediate logging. The agent gets zero diagnostic information beyond "it didn't crash."

**Why this matters:**

Without constraint tracking (P11), each `path_not_reached` result is a dead end. The agent can only guess what went wrong and try a different PoC variant. This leads to the observed pattern of many similar PoCs all failing the same way because the same constraint (e.g., "use SFW format, not JPEG") is never discovered.

**Proposed fix direction:**

1. **Constraint-aware failure diagnosis**: After `path_not_reached`, check which path constraints have been confirmed. If any are still "unknown" or "hypothesized", explicitly prompt the agent to verify them before generating more PoCs.
2. **Progressive narrowing**: Instead of blind PoC generation, add a tool or workflow step that validates individual constraints (e.g., "verify that the SFW parser accepts this file" before "verify that it reaches GenerateEXIFAttribute").
3. **Format validation tool**: Add a `ValidateFormat(path)` tool that checks if the PoC file is a valid carrier for the target parser without submitting to the server. This would catch format-level errors without wasting a submit.

---

## P13: Phase transition from investigation→formulation is premature

**Severity:** Medium — agent is pushed to formulate before understanding the path

**Evidence:**

The PhaseEngine auto-transitions from investigation to formulation:

```python
PhaseSpec(
    name="investigation",
    transitions=[
        TransitionRule(target="formulation",
            condition=lambda s: bool(s.trigger_hypothesis or s.vulnerable_functions or s.vulnerable_files),
            priority=10),
        TransitionRule(target="formulation",
            condition=lambda s: _phase_local_steps(s) >= 7,  # force after 7 steps
            priority=0),
    ],
)
```

The first rule transitions as soon as the agent has *any* trigger hypothesis, vulnerable function, or vulnerable file. In practice:
- Step 1-2: Agent finds `GenerateEXIFAttribute` in GREP → `vulnerable_functions` set → immediately transitions to formulation
- The agent has identified the sink but not the path to reach it

The second rule forces transition after 7 steps regardless. This means even if the agent is still mapping the path, it gets pushed to "make a PoC now."

The result: the agent constructs PoCs targeting the sink (GenerateEXIFAttribute) without understanding the entry path (SFW format → JPEG decode → EXIF extraction → IFD parsing), leading to guaranteed `path_not_reached`.

**Proposed fix direction:**

1. The investigation→formulation transition should require at least **one confirmed path constraint** beyond "the vulnerable function exists." Knowing the sink is necessary but not sufficient.
2. The `force_at_step=7` should be conditional — if the agent is making progress (discovering new constraints), extend the investigation budget.
3. Consider adding a `path_ready` signal that gates formulation: "I have confirmed enough of the entry→sink path to attempt a PoC."

---

## Architectural Summary: The Constraint Collection Gap

```
Current flow:
  Ingest → Investigate (find sink) → Formulate (guess PoC) → Submit → path_not_reached → Re-read → Budget block → Forced submit → repeat

Missing flow:
  Ingest → Investigate (map entry→sink path) → Collect constraints → Verify constraint completeness → Formulate PoC satisfying all constraints → Submit
```

The core architectural gap is that the agent has no structured representation of the **path constraint chain** from entry to sink. Without this:

- It cannot know when it has collected enough information to formulate a viable PoC
- It cannot diagnose *why* a PoC failed (which constraint was violated)
- It cannot prioritize reading to fill the most impactful knowledge gap
- The budget system cannot gate on meaningful progress (constraint completion), only on raw action count

The fix requires:
1. **Structured constraint model** (P11) — `PathConstraint` in state
2. **Constraint-aware budget** (P10) — gate on constraint completeness, not read count
3. **Constraint-aware failure diagnosis** (P12) — use constraints to explain `path_not_reached`
4. **Constraint-aware phase transition** (P13) — require minimum constraint coverage before formulation

These are interdependent: P11 is the foundation that enables P10, P12, and P13.
