# V13 Context Design Principles

This document captures the design principles behind the V13 observation rendering
layer. Use it as a reference when adding new features or modifying existing ones,
to ensure the context the LLM receives remains coherent and efficient.

---

## 1. Context = Investigation Brief, Not Controller State Dump

Every line in the observation must help the LLM build or refine a PoC. If a line
exists only because it's "state we track", it doesn't belong in the observation.
Internal bookkeeping (fingerprints, injection flags, checkpoint booleans) stays
in `state.metadata` and never reaches the LLM.

**Test**: For any line in the observation, ask: "Does this help the LLM decide
what to do next?" If the answer is "no, it's just metadata", remove it.

---

## 2. Six-Section Structure

The observation is organized into exactly 6 sections, in this order:

1. **Mission** — task identity (vulnerability, bug type, crash type, strategy, input format)
2. **Current Assessment** — what the agent believes right now (confirmed / likely / unknown / rejected)
3. **Vulnerability Path** — call chain diagram with per-node gate status
4. **Required Conditions** — PoC-relevant constraints with confirmation status
5. **Experiments** — PoC attempts with differential analysis
6. **Next Action** — blocking gap + recommended action

No additional `##`-level sections may be appended after these 6. Supplementary
data (static analysis results, code index context) must be consumed by the
section renderers, not appended as orphaned blocks.

---

## 3. Phase-Adaptive Visibility

Sections appear, compact, or hide based on what's useful at each phase:

| Section | Ingestion | Exploration | Investigation | Formulation | Verification |
|---------|-----------|-------------|---------------|-------------|--------------|
| Mission | Full (4 lines) | Compact (3 lines) | Compact | Compact | Compact |
| Current Assessment | Sink+harness focus | Sink+harness | Path+mechanism | Conditions focus | Feedback focus |
| Vulnerability Path | Hidden | Partial | Full diagram | Full+gate status | Full+feedback |
| Required Conditions | Hidden | Hidden/minimal | Inferred+confirmed | All+numerical+refuted | All+refuted |
| Experiments | Hidden | Hidden | Hidden/minimal | Table (last 3) | Table+analysis |
| Next Action | "Classify crash+READ harness" | "Record sink" | "Trace path" | "Build PoC" | "Interpret feedback" |

---

## 4. Provenance Tagging

Every factual assertion in the observation carries a `[source: ...]` tag so the
LLM knows what to trust and what to verify:

- `[source: code reading]` — agent read the source and confirmed
- `[source: model_candidate]` — LLM proposed, not yet code-verified
- `[source: auto_promoted]` — system auto-promoted from navigation lead
- `[source: description]` — extracted from vulnerability description (lower confidence)
- `[source: submit_poc feedback]` — confirmed or refuted by actual PoC execution
- `[source: analysis service]` — from static analysis / interprocedural analysis
- `[source: description_anchor_stale]` — regex-extracted from description, likely noise

Without provenance, the LLM treats all information equally and may over-trust
description-derived guesses or under-trust code-verified facts.

---

## 5. Information Lifecycle

Information in the observation has a lifecycle, not permanent residence:

| Category | Appears | Disappears |
|----------|---------|-----------|
| Description-derived sink candidates | Step 0 | Step 5 (move to Rejected, then drop) |
| Harness "ambiguous" status | Step 0 | When agent READs a harness file (auto-resolve) |
| "None recorded yet" sink prompt | Step 0 | When first sink recorded |
| Crash Type prompt | Step 0 | When crash_type is set |
| Auto-deepen hints | On trigger | After 3 steps |
| Refuted gates | On refutation | After 5 steps or next PoC attempt |
| Raw feedback records | On submit | After 2 newer records |
| Rejected assessment items | On rejection | After 3 steps |

**Rule**: If information hasn't changed and isn't actionable, it should decay
or compress. Permanent repetition wastes tokens and trains the LLM to ignore
the observation.

---

## 6. Delta Rendering

Between steps within the same phase, unchanged sections compress to save tokens:

```markdown
## Changes since step 5
- UPDATED: Current Assessment
- NEW: PoC experiment result
- UPDATED: Required Conditions

[Only changed sections rendered in full]
```

Full brief is always generated at:
- Step 0 (initial)
- Phase transitions
- After context compaction (PostCompactRestorer forces full regen)
- Every 10 consecutive delta-only steps (periodic refresh)

**Critical implementation note**: Previous hashes must be deep-copied BEFORE
writing new hashes to state.metadata. Since `state.metadata` is a mutable dict,
writing new hashes then reading "old" hashes reads the same object reference,
making delta comparison always match (no delta ever shown).

---

## 7. No Raw IR/HTML in Observation

All analysis IR (SinkAnalysisBrief, ReadAnalysis, etc.) must go through
`IRRenderer` before reaching the LLM. The LLM should never see:
- `html.escape(str(dict))` output like `&#x27;requirement_id&#x27;: &#x27;req_abc&#x27;`
- Raw JSON dicts from analysis services
- XML blocks with unformatted content

`IRRenderer` is in `agent_impl/ir_renderer.py` and provides pure functions
(render_requirement, render_path, render_gap, render_target, etc.) that convert
structured IR to human-readable Markdown.

---

## 8. Single Source of Truth Per Concept

No concept should appear in multiple observation sections with different names:

| Old (V12) | V13 Section | Location |
|-----------|-------------|----------|
| Sink Candidates | Current Assessment > Confirmed/Likely | One place |
| Active Sink Candidates | Current Assessment > Confirmed | One place |
| Constraint Board | Required Conditions | One place |
| PoC Requirements | Required Conditions | One place |
| PoC Byte Layout | Required Conditions (numerical constraints) | One place |
| Constraint Coverage | Vulnerability Path (per-node gate status) | One place |
| Unresolved Questions | Required Conditions (open gates with `?`) | One place |
| Latest Hot Feedback | Experiments table | One place |
| Failure Summary | Experiments table interpretation | One place |
| Current Objective | Next Action | One place |

**Rule**: When adding a new piece of information, check whether an existing V13
section already covers that concept. If so, extend the existing renderer rather
than adding a new section.

---

## 9. Static Analysis Consumed by Section Renderers

Data from `_inject_static_analysis_brief()` (sink search leads, analysis brief,
code index context) must be consumed by V13 section renderers, not appended as
standalone `##`-level sections after the 6-section brief.

The flow should be:

```
Static analysis data → state fields → V13 section renderers → observation
```

NOT:

```
Static analysis data → Markdown string → append after observation
```

Only `<static_index_status>` may be appended as metadata (it's small and doesn't
duplicate any V13 section).

---

## 10. Prompt-Observation Alignment

Prompts must reference the same section names the LLM sees in the observation.
If the observation says "Required Conditions", prompts should say "Required
Conditions", not "PoC Requirements" or "Constraint Board".

**Common misalignment patterns to avoid**:
- Prompt says "check the Constraint Board" but observation has no such section
- Prompt says "read PoC Requirements" but observation says "Required Conditions"
- Prompt says "check Sink Candidates list" but sinks appear in Current Assessment

**Rule**: After any change to section names or structure, grep all prompt files
for old names and update them.

---

## Anti-Patterns to Avoid

1. **Appending orphaned sections** — Don't add `## Extra Info` after the 6 sections.
   Put the data into an existing section renderer.

2. **Repeating the vulnerability description** — It appears once in Mission,
   compressed to 1 line. Don't repeat it in Current Assessment, Required
   Conditions, or Next Action.

3. **Showing "None recorded yet" prompts** — If no sinks are confirmed, the
   Current Assessment > Confirmed section simply lists "(nothing yet)" once.
   Don't add a separate instructional prompt about recording sinks.

4. **Gate count statistics without semantics** — "2/3 gates confirmed" is noise.
   Instead, show each gate with its status symbol (✓/?/✗) and what evidence
   confirms or refutes it.

5. **AFL boilerplate in feedback** — The harness's "This binary is built for
   AFL-fuzz" message is not actionable. Extract only the outcome and the
   ASAN signal (if any).

6. **Permanent WARNING lines** — Description-derived sink warnings that repeat
   from step 0 to step 50 are pure noise. Apply TTL or move to Rejected.

7. **Same-object-reference mutation in delta** — When comparing previous vs
   current section hashes, deep-copy the previous hashes BEFORE writing the
   new ones to `state.metadata` (which is a mutable dict).

---

## Known Technical Debt (from V13 audit)

These items were identified during the V13 audit but deferred for later cleanup:

### Dead renderers (safe to remove when TUI migrates to V13 sections)

These methods in `observations.py` are no longer called from the V13 path.
They still exist because the TUI metadata path or tests reference them:

- `_render_task_context_sections` — never called, has latent NameError at line 379
- `_working_memory_lines` — never called from V13
- `_project_memory_lines` — never called from V13
- `_constraint_board_lines` — called only from `tests/test_agent.py:541`
- `_task_memory_lines` — never called from V13
- `_strategy_memory_lines` — never called from V13
- `_state_block_lines` — never called from V13
- `_allowed_tool_lines` — never called from V13 (replaced by `_render_phase_tools`)
- `_recent_exploration_note_lines` — never called from V13

### State fields not yet consumed by V13

These fields are written during runs but not displayed in any V13 section:

- `vulnerability_analysis` — task-persistent analysis summary
- `path_trace` — separately maintained from `call_chain_nodes`, can diverge
- `attempt_history_compact` — different source than `hot_feedback_window`
- `current_hypothesis` — set by reduce() but not shown
- `read_coverage` — which files/line ranges have been read
- `harness_signals` — harness metadata
- `exploration_notes` — appended by all tracking tools
- `reflection_note` / `reflection_history` — set by record_reflection
- `repeated_failure_signature` / `repeated_failure_count` — failure pattern tracking
- `pending_reminder` / `pending_reminders` — one-shot reminders
- `verification_history` — history of all verification results
- `active_sink_candidate_id` — which sink is active (NOW SHOWN with ◀ ACTIVE tag)
- `analysis_status` — TARGET_PROPOSED etc.

### Fix approach for future

When adding new features, check whether the new state data should flow into
an existing V13 section before creating a new section. The 6-section structure
is intentionally fixed — new data should be integrated, not appended.

---

## Trace-Verified Anti-Patterns (from 18-run V13 test)

These anti-patterns were discovered by analyzing actual observation.md output
from 18 test traces (312 steps total). Unit tests did NOT catch these because
they test individual renderers in isolation, not the end-to-end observation
quality that the LLM sees.

### 11. Never extract "symbols" via broad regex from descriptions

`_symbol_mentions()` used `\b[A-Za-z_][A-Za-z0-9_]{2,}\b` which matches every
English word of 3+ characters. A 14-word blocklist missed "the", "occurs",
"after", "delete", "vulnerability", "function", "buffer", "uninitialized", etc.
These became description_symbol sink candidates at conf=0.3 that appeared in
every observation from step 0 to the final step.

**Rule**: Only extract identifiers that look like code: `func_name()` syntax,
multi-underscore snake_case, or CamelCase. Common English words should never
become sink candidates. When in doubt, return fewer candidates rather than more.

### 12. Auto-promotion must preserve provenance and reject low-confidence noise

When `_auto_promote_sink()` promotes a candidate, it must:
1. Store `original_source` in metadata before changing source to "model_candidate"
2. Apply a confidence threshold (>=0.5) to description-derived candidates
3. `confirmed_sink_candidates()` must check `original_source` against the
   provisional_sources blocklist, not just the current source

Without these safeguards, noise words like "buffer", "uninitialized", "function"
get promoted into Confirmed with ◀ ACTIVE, bypassing all filters.

### 13. Vulnerability descriptions need at least 300 chars

120 chars consistently cuts off the most important technical details (buffer sizes,
function parameters, trigger conditions). The description is the single most
important context the LLM receives. During ingestion, show up to 500 chars.
In later phases, 300 chars. For descriptions exceeding the limit, use
sentence-level scoring to keep the most informative sentence.

### 14. Ingestion phase must enforce crash_type classification

`_ingestion_ready()` must not transition on step 0 just because
`_generate_sink_candidates()` pre-populated provisional candidates. The LLM
must call `set_crash_type()` before leaving ingestion (with a 4-step fallback).
The ingestion phase prompt must explicitly state this is MANDATORY.

### 15. Required Conditions must be deduplicated and capped

Analysis service hazards can produce dozens of conditions, duplicated across
"requirements" and "triggers" sections. Cap at 12 total, deduplicate with a
`seen_conditions` set, and filter out nonsensical conditions like "8.0 == 0".

### 16. Stale description candidates must be properly eliminated

Marking a candidate as `description_anchor_stale` and lowering confidence to 0.29
is insufficient — it still appears in `navigation_candidates()` via
`requires_review=True`. Set `status="eliminated"` and `requires_review=False`
so the candidate is fully removed from all active lists.
