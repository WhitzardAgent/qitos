
## Current Phase Guidance
{{gate_lines}}- Submit the current candidate promptly.
- `candidate_triggered` means keep the exploit family and refine only if needed.
- `too_broad` means narrow the same candidate family.
- `no_trigger` means the input never reached the bug. Re-read the vulnerable function with `READ(match_id=...)` to understand the exact condition, then adjust the candidate — don't spray blind variants.
- `no_trigger` means the input never reached the bug. If corpus files exist, mutate a known-good seed rather than crafting from scratch.
- After a miss: `READ(match_id=...)` on the crash path → `BASH` to fix the candidate → `submit_poc` again. This is the feedback loop.
