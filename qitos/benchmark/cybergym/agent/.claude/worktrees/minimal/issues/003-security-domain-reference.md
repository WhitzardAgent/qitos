# Security Domain Knowledge Reference: Path & Constraint Collection

Extracted from `agentic-poc` (security experts' implementation), focusing on **security methodology**, not agent/prompt engineering.

---

## 1. Entry→Sink Path Must Be Traced as a First-Class Object

**What they do:** The very first subsection of working memory is `输入与验证`, and its item 1 is always:

> **harness source代码与input映射关系（重要，必须首先求解）**: identify the official harness/entry in source code; from the entry function start, trace raw artifact/input buffer and size through every source-level consumption/transformation until the target library/parser call; record the parser-visible pointer/slice/length.

Key security insights:
- The harness entry is NOT just "README says submit a file." You must find the **source code** of the entry function (e.g., `LLVMFuzzerTestOneInput`).
- From that entry, you must **trace every consumption/transformation** of the input buffer: pointer increments, size decrements, slicing, mode-byte reads, seed/prefix consumers (`FUZZ_seed`, `FuzzedDataProvider/Consume*`).
- The trace ends at the **parser-visible pointer/slice/length** — what the vulnerable parser actually sees, which may NOT be offset 0 of your PoC file.
- If this mapping is unknown, mark it `[待验证]` and **block all construction** until it is resolved.

**Our agent's gap:** We never trace the input mapping. We read `README.md` and `description.txt`, find the vulnerable function name, and start constructing PoCs. We don't know how bytes flow from the fuzzer entry to `GenerateEXIFAttribute`.

---

## 2. Parser Gates: The Constraint Model

**What they do:** They model PoC constraints as **positive parser gates** plus the vulnerability bad-state predicate:

> When possible, express PoC约束 as evidence-backed positive parser gates plus the vulnerability bad-state predicate: what source predicate must hold, and which artifact bytes/fields determine it.

A **parser gate** is a specific branch condition in the parser that must be satisfied for input to progress toward the vulnerable code. Examples:
- "SFW magic bytes `0x53 0x46 0x57` at offset 0 must be present for SFW decoder to accept input" (format gate)
- "EXIF tag count `nde` must be > 0 for IFD loop to execute" (path gate)
- "format_bytes[fmt] must be 0 for the vulnerable branch" (trigger gate)

The **bad-state predicate** is the specific condition that makes the vulnerability manifest:
- "format_bytes[0] access when `fmt=0` causes out-of-bounds read because `format_bytes[0]=0`"

**Critical discipline:**
- Only **evidence-backed** gates go in PoC约束. Weak/inferred/missing gates go in the plan as open items.
- Do NOT invent missing gates for completeness. Missing constraints are tracked separately.
- Reaching the target parser/function is only a **reachability constraint**, not enough to mark constraint extraction complete. You must also have at least one **vulnerability-specific bad-state/invariant-break/trigger constraint**.

**Our agent's gap:** We have no concept of parser gates. Our `trigger_hypothesis` is free text. We have no structured way to distinguish "I know the function name" (reachability) from "I know what bytes must be set" (trigger).

---

## 3. Ordered Workflow Phases Gated by Constraint Completeness

**What they do:** Their PoC推进计划 (PoC Progress Plan) has strictly ordered phases:

```
信息收集 → PoC约束提取 → 构造方案 → 构造 → 本地核验 → 验证/修复
```

With hard gating rules:
- **Do not execute a later row while any earlier row is still [未完成] or [进行中].**
- `[PoC约束提取]` cannot be marked complete until `构造记忆/PoC约束` includes at least one vulnerability-specific bad-state, invariant-break, or exploit-specific trigger constraint.
- `[构造方案]` cannot start until required `信息收集` and `PoC约束提取` rows are closed.
- `[构造]` completion requires artifact/tool evidence, not just file existence.
- After failed validation, **reopen earlier rows** if their progress is contradicted.

**Our agent's gap:** Our PhaseEngine transitions investigation→formulation as soon as we have `vulnerable_functions` (reachability only). There is no gate requiring trigger constraints before formulation begins.

---

## 4. Failure Classification for Diagnostic Repair

**What they do:** They classify every failure into one of:

| failure_class | Meaning | Repair direction |
|---|---|---|
| `carrier_invalid` | Input format rejected at parse entry | Fix carrier format/headers |
| `path_not_reached` | Parser accepted but didn't reach vulnerable code | Revisit input mapping and parser gates |
| `target_substructure_missing` | Reached code but wrong inner structure | Fix inner field layout |
| `trigger_condition_unmet` | Reached vulnerable code but trigger didn't fire | Change trigger bytes |
| `oracle_unclear` | Validation result ambiguous | Check oracle assumptions |
| `generator_runtime_error` | Generator script crashed | Fix generator |

For `path_not_reached` specifically:
> After path_not_reached or repeated exit_code=0, first suspect weak/incomplete PoC约束, artifact-view or harness-view mismatch, constraint solving, or local-check interpretation. ... first revisit 构造记忆/输入与验证 item 1 and weak parser gates before changing route/version/parser scope.

**Our agent's gap:** We classify into `carrier_parse`, `path_not_reached`, `malformed_substructure`, `wrong_trigger`, `timeout_not_crash` — similar taxonomy but the repair guidance is generic. We don't specifically route `path_not_reached` back to "revisit input mapping and parser gates." Our feedback says "READ the parser entry" but the budget blocks further reading.

---

## 5. Local Verification Before Official Submit

**What they do:** Before official validation, they compare the current artifact against known constraints using local evidence (artifact-view: hexdump/inspector output; harness-view: input mapping). GDB-based runtime checks are available but optional and non-gating.

**Key principle:** "Cheap precise local checks when they directly test current PoC约束." For example:
- Hexdump the PoC and verify the magic bytes at the correct offset match the format gate
- Use toolbox inspectors to verify structural fields (tag count, IFD offset) match what source code expects
- Compare artifact bytes against the input mapping to confirm parser-visible offset is correct

**Critical discipline:**
- After `path_not_reached`, **re-check the nearest upstream unverified gate** using source or artifact-view evidence before broad mutation.
- Local checks are diagnostic. Official validation is the only authoritative signal.

**Our agent's gap:** We have no local verification capability. Every check requires a full submit to the remote server, which only returns binary pass/fail. We cannot cheaply verify "do my PoC's bytes even match the format I'm targeting?" before submitting.

---

## 6. Input Mapping Must Explicitly Check Fuzzer Prefix Consumers

**What they do:** They specifically call out common fuzzer input prefix patterns:

> Explicitly check fuzzer seed/prefix consumers such as FUZZ_seed/FUZZ_RNG_SEED_SIZE, FuzzedDataProvider/Consume*, pointer increments, size decrements, slicing, or mode-byte reads before concluding direct input or offset 0.

Many fuzzers consume the first N bytes of input for their own purposes (RNG seed, mode selection, etc.) before passing the remainder to the target parser. If you don't account for this, your PoC's "offset 0" doesn't correspond to the parser's "offset 0."

**Our agent's gap:** We never check for fuzzer prefix consumption. We assume the PoC bytes map directly to the parser's input.

---

## 7. Constraint Reopening After Validation Failure

**What they do:**

> After failed validation, re-check earlier [已完成] 信息收集/PoC约束提取/构造方案/构造/本地核验 rows. If their 具体进度 is contradicted or insufficient, reopen them to [进行中] or [未完成] with updated concrete 具体进度.

This means: a `path_not_reached` result doesn't just mean "try another PoC." It means "one of your confirmed constraints was wrong." You must go back and question which constraint was incorrect.

**Our agent's gap:** After `path_not_reached`, our agent tries to construct another PoC variant but doesn't systematically question which of its assumptions was violated. The `record_reflection` is a free-text note, not a structured constraint audit.

---

## Summary: What We Should Adopt

| Security Concept | Our Current State | What to Build |
|---|---|---|
| Input mapping trace (entry→parser-visible pointer) | None | First-class state field, block construction until resolved |
| Parser gates as constraint model | `trigger_hypothesis` (free text) | Structured `PathConstraint` list with evidence status |
| Phase gating on constraint completeness | Phase transitions on `vulnerable_functions` (reachability) | Gate formulation on at least one trigger constraint |
| Failure-class-aware repair | Generic "READ the parser entry" | Route `path_not_reached` back to constraint audit |
| Local verification (artifact-view) | None (remote submit only) | Hexdump/inspector checks against constraints before submit |
| Fuzzer prefix consumer awareness | None | Explicit check in input mapping |
| Constraint reopening after failure | `record_reflection` (free text) | Structured constraint audit with reopen mechanism |

The fundamental insight from the security experts: **PoC construction is a constraint satisfaction problem, not a search problem.** You don't "try PoCs until one works." You collect constraints until you have enough evidence to construct a PoC that satisfies all of them, then verify locally, then submit.
