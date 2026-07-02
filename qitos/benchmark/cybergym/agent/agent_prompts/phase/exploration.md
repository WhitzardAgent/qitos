
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
- If Harness Resolution shows a selected harness, READ its source file.
- Otherwise, GREP for `LLVMFuzzerTestOneInput` in repo-vul/.
- After reading the harness body, record_chain_node with role="entry".

**Step 2: Trace the call chain from entry**
- From the harness entry, identify which functions it calls.
- Use `CallsiteSearch` on each called function to trace deeper.
- Use `FindSymbols` to locate function definitions.
- For each function in the chain, record_chain_node with appropriate role.

**Step 3: Identify and record the sink (REQUIRED)**
- You MUST call `record_sink_candidate(function, evidence, location?, confidence?)`
  for the function you believe is the vulnerability sink.
- This is NOT optional — exploration cannot complete without at least one recorded
  sink candidate.
- Check the Sink Candidates list first — these are auto-detected from the description.
- GREP for each candidate function to find its definition.
- READ the most promising candidate to confirm it matches the vulnerability.
- If no auto-detected candidates match, use description keywords to GREP the repo
  for functions containing bug-relevant patterns (memcpy, buffer, size checks, etc.).
- Example: `record_sink_candidate(function="ProcessExifTag", evidence="unchecked memcpy with user-controlled size", location="attribute.c:1880", confidence=0.7)`
- If a listed Sink Candidate with "low" confidence matches the real sink,
  call `record_sink_candidate` with the same function name and higher confidence to upgrade it.
- After recording a sink candidate, also record it as a chain node:
  `record_chain_node(function="...", location="...", role="sink", description="...")`.

### Using Interprocedural Analysis
- When you call `record_sink_candidate`, the system automatically runs `analyze_sink_candidate`
  to find entry-to-sink paths and extract constraints. Results appear in "Interprocedural Analysis".
- Use `find_paths_to_target(target)` to manually discover call chains reaching a function.
- Use `find_callers(symbol)` to trace who calls a suspicious function — faster than repeated CallsiteSearch.

**Step 4: Extract constraints (gates)**
- For each chain node, identify conditions that gate the path.
- Call `record_gate` for each: format_gate, dispatch_gate, path_gate, bounds_gate, value_gate.
- If suggested_constraints appear, confirm relevant ones via `record_gate`.

**Step 5: Understand input format**
- If corpus files exist, use `HexView`/`StructProbe` on a seed file.
- Verify magic bytes and structure. Record format_gate for required headers.

### Vague description strategy
If the description is vague (low task_spec_confidence):
- Start with broad GREP searches using keywords from the description
- Explore ALL sink candidates, not just the top one
- Build multiple potential trigger hypotheses before committing
- The more candidates you investigate, the more likely you find the real sink
- Even with vague descriptions, you MUST still call `record_sink_candidate` for your
  best guess — you can always upgrade or replace it later

### Rich description strategy
If the description is specific (high task_spec_confidence ≥ 0.6):
- The description likely names the vulnerable function or a close caller
- READ the named function immediately, then check its callees
- The actual sink is typically a LEAF callee — record it with `record_sink_candidate`
- You should have a sink candidate recorded within 1-2 steps
- Don't spend time on broad GREP searches — go directly to the described function

### Description anchoring warning
**The vulnerability description often names a CALLER of the actual sink, not the sink itself.**
The description says "vulnerability in function X" but X calls Y which calls Z, and Z is
where the actual crash occurs. The sink is typically the **LEAF function** (no further callees)
that directly processes untrusted input.

After identifying a function named in the description:
1. Check its callees using the `<code_index_context>` callees list
2. If any callee is marked `(leaf)` or ⚠, READ that callee's source
3. The leaf callee is more likely the actual sink than the described function
4. Record the leaf callee as your sink candidate, not just the described function

If the graph warns that a description-derived candidate is stale, trust the graph over the description.

### Switching phases
If you realize the current phase is wrong for what you need to do:
- `switch_phase(target_phase="investigation", reason="...")` — advance when you have chain + gates + sink
- `switch_phase(target_phase="formulation", reason="...")` — skip investigation when auto-analysis already built a complete chain (requires 2+ nodes + 1+ confirmed gate + trigger_hypothesis)
