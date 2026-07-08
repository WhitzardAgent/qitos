## Current Phase Guidance: Build a Source-Backed Starting Point

Use the six-section runtime brief as the source of truth. In ingestion, your
first job is to convert the vulnerability description into structured priors,
then verify those priors against code before treating anything as a sink.

### Step 1: Structure the description

If `description_analysis` is pending, call `analyze_description(...)` once.
Capture:

- likely vulnerability class and sanitizer crash-type prior;
- access mode (`read`, `write`, `free`, `call`, `control`, or `unknown`);
- memory region (`heap`, `stack`, `global`, `container`, or `unknown`);
- mechanism tags, operations, lifecycle/state transitions, numeric facts;
- suspect functions/files/modules/parameters and trigger conditions.

Do not call a description-derived function a confirmed sink. It is only a
navigation prior until verified by code or `submit_poc` feedback.

The strongest crash-type source
is always `submit_poc`; a description crash type is only a prior.

### Step 2: Read verified anchors first

After analysis service verification, prefer `Current Assessment > Likely`
verified refs over broad text search. read the top verified ref or selected
harness file and decide whether the code is:

- `crash_site`;
- `causal_site`;
- `path_anchor`;
- `dangerous_primitive`;
- or only a caller/non-sink.

### Step 3: Establish the harness-to-sink hypothesis

Find the fuzzer entry and first input consumers. When you have source-backed
evidence, record the entry with `record_chain_node(...)` and record the best
sink candidate with `record_sink_candidate(...)`.

### Key principle

Every fact in the runtime brief has provenance. Treat `[source: description
prior]` as a hypothesis, `[source: analysis service]` as a source-backed lead,
and `[source: submit_poc]` as the highest-priority oracle.
