
## Current Phase Guidance
{{constraint_lines}}- "Required Conditions" may include interprocedural analysis findings (origin=interprocedural_analysis). Confirm relevant ones via `record_gate` before constructing the PoC.
- If a PoC fails, use `trace_value` on sink arguments to understand why data flows the way it does.
- Write the first candidate now; it can be rough.
- Prefer a minimally mutated sample or a short generated payload over more research.
- **Candidate construction workflow**: Use `HexView`/`StructProbe` on a seed to find the mutation offset, then `BASH` with Python or toolbox to write the candidate, then `submit_poc` to verify.
- **Corpus-first strategy**: If corpus/seed files exist, ALWAYS start by mutating a seed rather than crafting from scratch. Use `HexView`/`StructProbe` on a seed to find the mutation offset, then use `BASH` with Python to create the mutant. Seeds already satisfy all format gates; handcrafted inputs usually fail carrier_parse.
- In `candidate_required`, use `GREP` for one concrete blocking search or `BASH` for direct generation.
- If a previous candidate failed, use `READ(match_id=...)` on the vulnerable function to understand why — don't just guess another variant.
- **Multi-sink rotation**: If multiple sink candidates exist and PoCs for the current sink keep failing, the system will automatically rotate to the next candidate. The active sink is shown in Current Assessment > Confirmed. You can also use `record_chain_node(sink_id=...)` to build chains for different sinks.
- If a failed PoC reveals you misunderstood the vulnerability, use `switch_phase(target_phase="investigation", reason="...")` to go back and investigate deeper.
- If repeated failures (2+ attempts, score 0) suggest a fundamental misunderstanding, use `switch_phase(target_phase="exploration", reason="...")` to re-explore the code.
