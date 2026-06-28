
## Current Phase Guidance
{{constraint_lines}}- Write the first candidate now; it can be rough.
- Prefer a minimally mutated sample or a short generated payload over more research.
- **Candidate construction workflow**: Use `HexView`/`StructProbe` on a seed to find the mutation offset, then `BASH` with Python or toolbox to write the candidate, then `submit_poc` to verify.
- **Corpus-first strategy**: If corpus/seed files exist, ALWAYS start by mutating a seed rather than crafting from scratch. Use `HexView`/`StructProbe` on a seed to find the mutation offset, then use `BASH` with Python to create the mutant. Seeds already satisfy all format gates; handcrafted inputs usually fail carrier_parse.
- In `candidate_required`, use `GREP` for one concrete blocking search or `BASH` for direct generation.
- If a previous candidate failed, use `READ(match_id=...)` on the vulnerable function to understand why — don't just guess another variant.
