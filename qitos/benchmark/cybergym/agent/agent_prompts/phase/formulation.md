
## Current Phase Guidance

### vNext hard requirements (Fix A)
- Before generating a PoC, you MUST reference the `recipe_id` from Required Conditions. If no recipe exists, ensure interprocedural analysis has been run first.
- If Required Conditions shows `recipe open_gaps`, resolve each gap before submitting. Only submit when open_gaps is empty.
- If Required Conditions shows harness selectors or delimiters, encode them in the PoC — a raw buffer is insufficient for multi-record/API-sequence/selector-based harnesses.
- If Next Action contract slot is non-empty, follow it instead of submitting immediately.

{{constraint_lines}}- "Required Conditions" may include interprocedural analysis findings (origin=interprocedural_analysis). Confirm relevant ones via `record_gate` before constructing the PoC.
- If a PoC fails, use `read` on sink arguments to understand why data flows the way it does.
- Write the first candidate now; it can be rough.
- Prefer a minimally mutated sample or a short generated payload over more research.
- **Recipe-first PoC construction**: When Required Conditions shows concrete mutation targets (offset, width, strategy), use them directly. If recipe has a seed_path, mutate the seed at the specified offset.
- If Required Conditions shows recipe open_gaps, resolve them before submit. Only submit when open_gaps is empty.
- If structured_rewrite_plans specify invariants, preserve them in the PoC.
- If Required Conditions shows harness selectors/delimiters, encode them in the PoC.
- Build PoCs from Required Conditions, not from memory. The current recipe is the authoritative plan.
- **Candidate construction workflow**: Use `hex_view`/`struct_probe` on a seed to find the mutation offset, then `bash` with Python or toolbox to write the candidate, then `submit_poc` to verify.
- **Corpus-first strategy**: If corpus/seed files exist, ALWAYS start by mutating a seed rather than crafting from scratch. Seeds already satisfy all format gates; handcrafted inputs usually fail carrier_parse.
- **Pre-submit sanity**: The system automatically checks PoC carrier validity before submit. If blocked by `CARRIER_SANITY_FAIL`, fix the carrier structure (magic, table directory, chunk headers) before retrying — the issue is NOT with your sink/path choice.
- **Font/SFNT/OTF/CFF2 PoCs**: Before mutating a font file, verify the SFNT table directory is valid. Only mutate the target table's payload — do not destroy the table directory or header.
- In `candidate_required`, use `grep` for one concrete blocking search or `bash` for direct generation.
- If a previous candidate failed, use `read(match_id=...)` on the vulnerable function to understand why — don't just guess another variant.
- **No-trigger iteration**: After a no-crash result, revise ONE mutation parameter (field value, offset, or trigger condition). Do not change the entire approach unless multiple axes have failed.
- **Negative evidence**: When Experiments shows 3+ no-trigger evidences for the same family, do NOT submit more variants. Replan: revise the mutation offset/strategy or rotate to a different sink candidate. A `no_crash_unknown` result does NOT prove path_not_reached — it could also be a trigger-condition miss.
- **Multi-sink rotation**: If multiple sink candidates exist and PoCs for the current sink keep failing, the system will automatically rotate to the next candidate.
- If a failed PoC reveals you misunderstood the vulnerability, the system will transition back to investigation when appropriate.
- If repeated failures (2+ attempts, score 0) suggest a fundamental misunderstanding, the system will transition back to exploration when appropriate.
