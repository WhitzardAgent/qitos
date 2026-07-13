# Model-summary tool projection

## Goal

Keep a tool's full structured output available to reducers, trace processors,
and replay, while ensuring that native tool-call history contains a bounded,
human-readable `model_summary` when a tool provides one.

## Scope

- Project `output["model_summary"]` for every tool in action history and
  model-visible observation events.
- Preserve the canonical `ToolResult.output` unchanged for reduction and
  trace persistence.
- Retain existing `submit_poc` verifier redaction when a tool does not supply
  a model summary.
- Validate the contract using CyberGym static-analysis and GDB examples.

## Non-goals

- Do not change tool execution semantics, benchmark verdicts, or action
  ordering.
- Do not alter the canonical trace payload or discard artifacts needed for
  replay.

## Verification

1. A tool with a `model_summary` reaches history as that exact string.
2. The same tool's canonical observation/reducer payload still contains the
   complete structured output.
3. A tool without a summary preserves existing behavior, including submit
   redaction.
