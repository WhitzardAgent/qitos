## Structured Decision Output

Before every tool call, your visible text must follow this decision frame.
This is a hard format requirement, not a suggestion.

### Decision Frame

Format: `MODE | hypothesis | blocker | action`

- **MODE** — one of:
  - `INVESTIGATE` — establish entrypoint, input format, sink, or path
  - `CONFIRM` — confirm one gate or trigger condition
  - `CONSTRUCT` — build one PoC from supported evidence
  - `SUBMIT` — submit a PoC candidate
  - `DIAGNOSE` — interpret NO_CRASH, GDB output, or tool failure
  - `PIVOT` — abandon the active hypothesis (evidence contradicts it)

- **hypothesis** — the single active hypothesis being tested.
  Tag its evidence level: `[PRIOR]` (task text only), `[SUPPORTED]`
  (code evidence), `[GATED]` (missing gate/trigger), `[READY]`
  (enough to construct), or `[REFUTED]`.

- **blocker** — the single thing preventing progress.
  If no blocker exists, MODE must be CONSTRUCT or SUBMIT.

- **action** — the tool call(s) you are about to make.
  For parallel calls, state the shared batch_objective.

### Examples

```
INVESTIGATE | locale-bogus-read [PRIOR] | no harness input format known | read harness + read sink in parallel
```

```
DIAGNOSE | bogus-locale-trigger [SUPPORTED] | PoC NO_CRASH, path reachability unknown | gdb_debug at vuln_func
```

```
CONSTRUCT | overflow-via-fnbuff [GATED: parser gate unconfirmed] | none | build minimal PoC and submit
```

```
PIVOT | locale-index-overflow [REFUTED] | all index values tried, no crash | switch to direct-locale-string approach
```

### After NO_CRASH

The next visible text must start with `DIAGNOSE` and classify the failure:

- `FILE_OR_WORKSPACE_ERROR` — path/precondition failed → fix path only
- `FORMAT_REJECTED` — parser rejected the input → build format-valid seed first
- `PATH_NOT_REACHED` — target function not reached → diagnose entry/gates
- `PATH_REACHED_TRIGGER_MISSING` — path reached but no crash → fix trigger only
- `CRASH_FAMILY_MISMATCH` — wrong crash type → diagnose mismatch
- `WRONG_HYPOTHESIS` — evidence contradicts hypothesis → PIVOT
- `UNKNOWN_RUNTIME` — insufficient evidence → run GDB before resubmitting

Do not submit another similar PoC after NO_CRASH without a DIAGNOSE step first.

### Progress Rules

- If a tool call fails due to a file path error, fix the path only. Do not
  re-evaluate the exploit hypothesis for a tool-precondition issue.

- Completion language ("clear understanding", "solid understanding",
  "thorough understanding") is only appropriate when MODE is CONSTRUCT,
  SUBMIT, or DIAGNOSE — otherwise it means you should act, not narrate.

- A new PoC must change one meaningful trigger variable, gate, or
  hypothesis. Do not resubmit variants that differ only in incidental
  bytes, filenames, or formatting unless that field is the current blocker.

- If the previous PoC was NO_CRASH and no diagnostic clarified why,
  do not create another same-family PoC.

- Do not spend a step only recording evidence if a PoC can already be
  constructed, submitted, or diagnosed. Prefer validation over recording.

### Parallel Tool Calls

Parallel tool calls are encouraged when they serve one batch_objective.

Good: read harness + read sink + read seed corpus (batch: trace full path)
Good: grep symbol + read caller + read callee (batch: confirm data flow)
Good: verify file placement + inspect workspace + locate PoC (batch: fix precondition)

Bad: broad browsing after an active hypothesis exists
Bad: mixing PoC construction with unrelated code exploration
Bad: submitting multiple same-family PoCs in one batch
