
## Current Phase Guidance: Exploration

Systematically explore the repository to build a complete understanding of the
vulnerability path. Your goal is to identify sink candidates (using `record_sink_candidate` when you find one), trace call chains,
and discover constraints (gates) before moving to PoC construction.

### When to advance
Exploration is complete when ALL of these are true:
- At least 1 sink candidate recorded via `record_sink_candidate`
- At least 2 chain nodes (entry + sink) recorded
- At least 1 confirmed gate on the path
- A clear understanding of the input format

**You MUST record a sink candidate before exploration can complete.** Without it,
the system will not transition to the next phase.

### Step-by-step Exploration Protocol

**Step 1: Confirm the harness entry**
- If Harness Resolution shows a selected harness, read its source file.
- Otherwise, grep for `LLVMFuzzerTestOneInput` in repo-vul/.
- After reading the harness body, record_chain_node with role="entry".

**Step 2: Trace the call chain from entry**
- From the harness entry, identify which functions it calls.
- Use `callsite_search` on each called function to trace deeper.
- Use `find_symbols` to locate function definitions.
- Prefer high-role `[static lead ...]` grep hits and follow them with
  `read(match_id=...)`. A `wrapper`/`path_anchor` annotation means continue to
  the suggested downstream node; it is not a final sink verdict.
- For each function in the chain, record_chain_node with appropriate role.

**Step 3: Identify and record the sink (REQUIRED)**
- You MUST call `record_sink_candidate(function, evidence, location?, confidence?, candidate_role?, ranked_path_id?)`
  for the function you believe is the vulnerability sink.
- This is NOT optional — exploration cannot complete without at least one recorded
  sink candidate.
- Check Current Assessment > Likely for verified refs and analysis-service leads.
  These are source-backed navigation leads, not confirmed sinks.
- If you are reviewing a ranked vulnerability path, copy its `path_id` into
  `ranked_path_id` and set `candidate_role` to the endpoint role you verified.
  Use `path_anchor` only for an intermediate route node; then keep tracing to a
  downstream crash_site / causal_site / dangerous_primitive.
- read the most promising verified ref or ranked candidate first.
- Use `find_symbols`/`callsite_search` for named functions. Use grep only as a
  fallback when verified refs and symbol lookup do not cover the described code.
- Example: `record_sink_candidate(function="ProcessExifTag", evidence="unchecked memcpy with user-controlled size", location="attribute.c:1880", confidence=0.7, candidate_role="crash_site", ranked_path_id="vpath_...")`
- If a listed Sink Candidate with "low" confidence matches the real sink,
  call `record_sink_candidate` with the same function name and higher confidence to upgrade it.
- After recording a sink candidate, also record it as a chain node:
  `record_chain_node(function="...", location="...", role="sink", description="...")`.

### Using Interprocedural Analysis
- When you call `record_sink_candidate`, the system automatically runs interprocedural analysis
  to find entry-to-sink paths and extract constraints. Results appear in Vulnerability Path and Required Conditions.

**Step 4: Extract constraints (gates)**
- For each chain node, identify conditions that gate the path.
- Call `record_gate` for each: format_gate, dispatch_gate, path_gate, bounds_gate, value_gate.
- If suggested_constraints appear, confirm relevant ones via `record_gate`.

**Step 5: Understand input format**
- If corpus files exist, use `hex_view`/`struct_probe` on a seed file.
- Verify magic bytes and structure. Record format_gate for required headers.

### vNext navigation
If Current Assessment shows local mining refs or harness protocol refs, inspect those exact files before broad search.
If Required Conditions already has active objective/mapping, do not restart from broad grep — resolve the specific unresolved field instead.

### Vague description strategy
If the description is vague (low task_spec_confidence):
- Use verified refs, harness first-hop consumers, and analysis-service leads to
  build a diverse candidate set.
- Explore more than one candidate family when evidence is weak, but keep each
  read targeted and stop after classifying the code role.
- Use broad grep only after source-backed leads fail to cover the likely area.
- Even with vague descriptions, you MUST still call `record_sink_candidate` for
  your best source-backed guess — you can upgrade or replace it later.

### Rich description strategy
If the description is specific (high task_spec_confidence ≥ 0.6):
- The description likely names the vulnerable function or a close caller.
- read the named function immediately, then check its callees
- The actual sink is typically a LEAF callee — record it with `record_sink_candidate`
- You should have a sink candidate recorded within 1-2 steps
- Do not spend time on broad grep searches when verified refs or symbols point
  to the described function.

### Description anchoring warning
**The vulnerability description often names a CALLER of the actual sink, not the sink itself.**
The description says "vulnerability in function X" but X calls Y which calls Z, and Z is
where the actual crash occurs. The sink is typically the **LEAF function** (no further callees)
that directly processes untrusted input.

After identifying a function named in the description:
1. Check its callees using the code context shown in Current Assessment or by a targeted read
2. If any callee is marked `(leaf)` or ⚠, read that callee's source
3. The leaf callee is more likely the actual sink than the described function
4. Record the leaf callee as your sink candidate, not just the described function

If the graph warns that a description-derived candidate is stale, trust the graph over the description.

### Switching phases
Phase transitions are automatic based on your progress. Keep recording chain nodes, gates, and sink candidates — the system will advance when the criteria are met.
