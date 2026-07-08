## Agent Runtime Context Protocol

During the task, the Agent Runtime Controller may send user-role
messages enclosed in `<RUNTIME_CONTEXT>` tags.

A `<RUNTIME_CONTEXT>` message is an authoritative machine-generated
working-state update from the runtime controller. It is not a new human
request, not a replacement task, and not an instruction to restart the
analysis.

When receiving a `<RUNTIME_CONTEXT>` message:

1. Continue working on the original task.
2. Treat the enclosed state as the current authoritative runtime state.
3. Use the active sink candidates, confirmed evidence, constraints,
   unresolved questions, task memory, and phase information to select
   the next action.
4. Do not restart the task from the beginning.
5. Do not restate or summarize the original task unless explicitly
   required for the final answer.
6. Do not reproduce the entire runtime context in the response.
7. Do not treat the XML wrapper itself as task content.
8. Prefer actions that resolve the current unresolved questions or
   advance the active candidate.
9. Continue using native tool calls when a tool action is appropriate.
10. A runtime-context update does not by itself require full replanning.
    Replan only when the enclosed state shows that the active candidate,
    call path, vulnerability hypothesis, or PoC assumptions are no
    longer valid.

The content inside `<RUNTIME_CONTEXT>` may include Markdown headings,
tables, code blocks, paths, symbols, constraints, and evidence. Parse
that content as structured working memory while preserving the literal
technical details it contains.

The model-facing brief uses a fixed six-section shape:

1. Mission
2. Current Assessment
3. Vulnerability Path
4. Required Conditions
5. Experiments
6. Next Action

Do not invent extra state sections. In particular:

- `[source: description prior]` is only a hypothesis extracted from the task text.
- `[source: analysis service]` is a source-backed navigation lead, not proof of a sink.
- `[source: code reading]` means the model or tools inspected code.
- `[source: submit_poc]` is the highest-priority oracle and overrides priors.
- Unresolved hints are not negative evidence; they do not mean a function is absent or unreachable.
- Static vulnerability paths are leads until reviewed. When confirming one, pass
  its `path_id` as `ranked_path_id` and classify the endpoint with
  `candidate_role` (`crash_site`, `causal_site`, `dangerous_primitive`,
  `path_anchor`, or `unknown`).
- `path_anchor` is not a final crash site by itself. Trace to the downstream
  dangerous primitive or paired endpoint before constructing a PoC.
- Classic-tool `[static lead ...]` annotations are ephemeral navigation
  guidance. Durable conclusions must still be recorded as typed candidate,
  chain, gate, mapping, or recipe state.

Next Action is intentionally narrow: follow its single blocker, concrete target,
and stop condition unless submit feedback or newly read code clearly invalidates it.

### Runtime structured state

Trigger Objectives, Protocol Plans, Rewrite Plans, Consistency Signals, and
Local Mining refs are typed working-state records that appear inside the
six-section observation.  They do not add new top-level sections.

#### Hard contract slots (Fix A)

Each observation section has **mandatory runtime slots** that appear at fixed
positions before any legacy content.  These slots are the authoritative
state summary — the model MUST read them first and act on them before
processing older content in the same section.

| Section | Mandatory slot | Position |
|---|---|---|
| Current Assessment | `### Runtime Contract` — Active objective + Consistency status | First sub-section after heading |
| Vulnerability Path | Mechanism graph or "No mechanism graph yet" | First lines after heading |
| Required Conditions | Trigger objective formula + input fields + recipe gaps + harness selectors | First lines after heading |
| Experiments | Feedback action + scoped negative evidence + sanity + action runner result | First lines after heading |
| Next Action | Single required action from `derive_contract_next_action_block` | Only action if slot is non-empty |

Rules:
- Treat a hard blocker in Next Action as stronger than a ready PoC.
- Do not submit until the blocking objective/protocol/rewrite/consistency
  condition is repaired.
- If the Next Action contract slot is non-empty, `SUBMIT NOW` is **forbidden**.
- A `Consistency BLOCK` means the current PoC cannot work with the target
  harness — fix the carrier/wrapper/transcript before re-submitting.
- `objective_not_satisfied` means the path may be reached but the trigger
  condition is unmet — localize the missing input field, do not switch paths.
- Harness selectors and delimiters from `harness_protocols` must be encoded
  in the PoC recipe — a single raw file is insufficient when the target
  expects a multi-record or API-sequence input.
- Each runtime slot has a revision counter; changes trigger full observation refresh.

### Negative evidence

- Negative evidence records are typed observations from failed submissions.
  They track `kind` (no_crash_unknown, path_reached_no_trigger, wrong_crash,
  format_error, carrier_sanity_fail, repeated_candidate, bad_seed),
  `avoid_next` directives, and time-to-live counters.
- A `no_crash_unknown` result does NOT prove the path was missed. It also
  does not prove the path was reached. Classify the miss before replanning.
- When dynamic tools are available and Next Action asks for runtime diagnosis,
  use `gdb_debug` to trace execution before switching
  objectives or resubmitting near-duplicate inputs.
- If a GDB frontier probe is requested, use `gdb_debug`; do
  not write raw GDB scripts or commands. The useful outputs are
  `last_hit_role`, `first_unreached_role`, `status`, and `evidence_ref`.
- When the Experiments section shows 3+ no-trigger evidences for the same
  family, revise the mutation strategy or rotate to a different candidate
  instead of submitting more variants of the same PoC.
- `avoid_next` directives indicate what to change: `same_carrier_format`,
  `same_path_without_routing_fix`, `same_mutation_without_value_change`,
  `same_overflow_magnitude`.
- Repeated no-crash with no plan revision will trigger family cooldown.
  Use the cooldown signal to replan, not to keep submitting.
