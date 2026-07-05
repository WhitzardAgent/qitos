
## Current Phase Guidance

### vNext hard requirements (Fix A)
- Before submit, you MUST state the Consistency status from Current Assessment. If it shows BLOCK, do NOT submit — fix the consistency issue first.
- If Next Action contract slot shows a required action (not SUBMIT NOW), follow it instead of submitting.
- `no_trigger` is ambiguous. Classify it against objective, transcript, carrier, consistency, and oracle kind before changing path.
- If Active objective shows `oracle_kind=msan`, verify the target harness actually runs with MSan before assuming no-trigger means path-not-reached.

### Oracle/sanitizer-aware guidance (Fix F)
- **MSan objectives** (`oracle_kind=msan`): If no-trigger diagnosis shows `oracle_not_observable`, the harness binary may not be built with MSan. Do NOT keep submitting crash variants. Verify the binary supports MSan, or switch_objective.
- **Semantic/parser oracle** (`oracle_kind=semantic_accept` or `parser_reach`): These bugs do NOT produce crashes. Do not submit expecting a crash signal. Instead, provide reachability proof or expected output.
- **ASan stack-use-after-return**: Requires `detect_stack_use_after_return=1`. If no-trigger diagnosis shows `oracle_not_observable`, this runtime flag may not be set.
- **Wrong harness diagnosis** (`no_trigger_diagnosis=wrong_harness`): The fuzzer binary may not match the target. Re-extract harness protocol and verify.
- After 2+ no-trigger for the same objective, check the no-trigger diagnosis before resubmitting. If it says `oracle_not_observable`, do NOT resubmit the same approach.

{{gate_lines}}- Submit the current candidate promptly.
- `candidate_triggered` means keep the exploit family and refine only if needed.
- `too_broad` means narrow the same candidate family.
- `no_trigger` is ambiguous. Classify it against objective, transcript, carrier, consistency, and oracle kind before changing path. It does NOT automatically mean path_not_reached.
- After a miss: `READ(match_id=...)` on the crash path → `BASH` to fix the candidate → `submit_poc` again. This is the feedback loop.
- **Negative evidence**: When Experiments shows repeated no-trigger evidence for the same family (3+), replan the mutation strategy or rotate to a different candidate. Do not submit more blind variants.
- **Replan vs. submit**: If Next Action says "Replan recommended", prioritize revising the mutation offset/value or rotating candidates before submitting the ready PoC.
- **Next Action priority**: A hard blocker (consistency, transcript gap, objective field, oracle context) overrides a ready PoC. Do not submit until the blocker is resolved.
