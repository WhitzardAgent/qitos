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

Next Action is intentionally narrow: follow its single blocker, concrete target,
and stop condition unless submit feedback or newly read code clearly invalidates it.
