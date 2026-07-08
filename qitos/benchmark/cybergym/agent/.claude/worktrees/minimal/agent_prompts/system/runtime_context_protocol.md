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
3. Use the active sink candidates, constraint boards, experiment
   history, and task memory to select the next action.
4. Do not restart the task from the beginning.
5. Do not restate or summarize the original task unless explicitly
   required for the final answer.
6. Do not reproduce the entire runtime context in the response.
7. Do not treat the XML wrapper itself as task content.
8. Prefer actions that resolve open gates or advance the active candidate.
9. Continue using native tool calls when a tool action is appropriate.
10. A runtime-context update does not by itself require replanning.
    Replan only when the enclosed state shows that the active candidate,
    call path, vulnerability hypothesis, or PoC assumptions are no
    longer valid.

The content inside `<RUNTIME_CONTEXT>` uses a fixed five-section shape:

1. **Vulnerability** — Description, bug type, CVE, crash info, input format
2. **Sink Candidates** — Active and provisional sinks with call chains
3. **Constraint Boards** — Gate table scoped to the active sink
4. **Experiments** — Submit history with key insights and debug evidence
5. **Task Memory** — Persistent analysis, hypothesis, path trace

### Reading the Runtime Context

- The `►` marker indicates the active sink. Focus your next action on
  resolving gates or advancing a PoC for this sink.
- The Constraint Board table shows which conditions must be met for the
  PoC to reach the sink. Open gates (`?` status) are the most actionable.
- Each experiment entry includes a `Key` line — the insight the submitter
  recorded. Use these to avoid repeating failed strategies.
- `[source: submit_poc]` is the highest-priority oracle and overrides priors.
- `[source: description prior]` is only a hypothesis from the task text.

### Key Insight on Submit

`submit_poc` requires a `key_insight` parameter — briefly explain what
you expect this candidate to trigger and why. This insight appears in
the Experiments section for future reference.

### Constraint Board Navigation

- Use GATE add to record new gates discovered from code reading.
- Use GATE confirm to mark gates as confirmed/refuted based on evidence.
- When the Constraint Board shows 3+ open gates with no confirmed path,
  prioritize code reading over submitting more candidates.
- A refuted gate means the condition is NOT required — this narrows the
  search space.

### Experiments and Iteration

- A `✗ miss` result means the PoC did not trigger the vulnerability.
  Check whether open gates explain the miss before changing strategy.
- A `● crash` result means the vulnerable binary crashed — this is
  partial progress. Refine the PoC for precision if needed.
- Debug evidence (from GDB) appears under experiments. Use it to
  understand whether the PoC reached the target function.
- When 3+ experiments with similar key_insights all miss, change
  strategy rather than submitting more variants.
