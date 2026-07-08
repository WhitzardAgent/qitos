# Archived Historical Plan

This file is a historical optimization plan derived from earlier trace analysis.
It is useful for background, but it is not the current architecture reference.
Use `ARCH.md`, `README.md`, and `docs/2026-04-26-cybergym-agent-current-status.md`
for the active implementation.

# Next-Phase Optimization Plan for CyberGym Agent

Based on analysis of 100 real test traces (73 pass / 27 fail) from Claude Code + GLM-5.1 on CyberGym tasks.

---

## Key Findings from Trace Analysis

### Statistical Summary

| Metric | Pass (n=73) | Fail (n=27) |
|--------|-------------|-------------|
| Mean turns | 72.3 | 31.9 |
| Median turns | 53 | 0 (most are timeouts) |
| Mean cost | $21.48 | $12.26 |
| Mean duration | 613s | 374s |
| Avg submit attempts | 6.9 | 39.6 |
| First-shot success | 18/73 (25%) | - |
| Multi-iteration success | 55/73 (75%) | - |

### Failure Classification

| Failure Mode | Count | Root Cause |
|-------------|-------|------------|
| Timeout (no PoC produced) | 20 | Agent never writes a working PoC within time |
| Same crash (vul_exit == fix_exit) | 5 | PoC is too aggressive, crashes both versions |
| Budget exhausted | 1 | 242 turns, $100+ |
| No trigger (vul_exit == 0) | 1 | Agent misidentified the vulnerability |

### Claude Code vs QitOS Agent: Critical Differences

1. **Claude Code uses `submit.sh` as the verification oracle** -- it gets immediate binary feedback (exit_code) on every attempt. The QitOS agent relies on phase-based reasoning without this tight feedback loop.

2. **Claude Code never uses `Write` tool** -- it writes PoC files exclusively via `Bash` commands (`python3 -c`, `cat >`, `printf`, `xxd -r`). This gives it more flexibility in generating binary/format-specific content.

3. **Claude Code reads `submit.sh` as one of its first 5 actions (100% of runs)** -- it understands the submission mechanism before investigating code. The QitOS agent doesn't have an equivalent "understand the harness" step.

4. **77% of successful runs use corpus/fuzzing samples as PoC starting points** -- the agent looks for existing test inputs (fuzzing corpus, sample files) and modifies them rather than building PoCs from scratch.

5. **75% of successful runs require multiple submit iterations** -- the agent submits early, gets feedback, and iterates. Average 6.9 submissions per successful run.

6. **PoC generation methods**: python3 -c (34%), cat heredoc (27%), xxd (21%), echo (10%), printf (4%) -- diverse binary generation strategies.

7. **Median first PoC at tool #29, first submit at tool #31** -- the agent transitions from investigation to formulation quickly after understanding the code structure.

---

## Design Optimization Proposals

### P0: Submit-as-Oracle Feedback Loop

**Problem**: The current QitOS agent uses PhaseEngine to transition between phases, but has no tight feedback loop during verification. It submits once and checks the result, but doesn't iterate with the rapid submit-analyze-modify cycle that Claude Code uses (6.9 iterations on average).

**Proposal**: Add a `SubmitIterateLoop` as a first-class pattern in the agent:

```
while poc_attempts < max_attempts:
    result = submit_poc(poc_path)
    if result.exit_code != 0 and result.fix_exit_code == 0:
        return SUCCESS  # PoC triggers vul but not fixed version
    elif result.exit_code != 0 and result.fix_exit_code != 0:
        # PoC is too aggressive -- both versions crash
        # Need to make PoC more specific to the vulnerability
        refine_strategy = "discriminant"  # Make PoC more targeted
    elif result.exit_code == 0:
        # PoC doesn't trigger the bug
        refine_strategy = "amplify"  # Make PoC trigger the specific path
    poc_path = refine_poc(result, refine_strategy)
```

**Implementation in cybergym_agent**:
- Add `submit_and_analyze()` method to `CyberGymAgent` that wraps `submit_poc` and returns a structured `PoCVerificationResult`
- In the verification phase, instead of a simple submit-then-check, implement the iterative loop
- The `reduce()` method should track `last_verification_result` with full context (vul_exit, fix_exit, stderr output)
- Add discriminant-aware prompting: when `fix_exit != 0`, instruct the model that the PoC is too aggressive and needs to be more targeted

**Why**: 75% of successful runs need multiple iterations. The single-submit pattern in the current agent misses this critical loop.

---

### P0: Harness-Aware Ingestion Phase

**Problem**: Claude Code reads `submit.sh` and `README.md` in the first 3 actions of every run (100% of runs). This gives it critical context about how the PoC will be verified, what binary is being tested, and what arguments it expects. The QitOS agent's ingestion phase doesn't have an explicit "understand the harness" step.

**Proposal**: Restructure the ingestion phase with explicit steps:

1. Read `README.md` -- understand task setup
2. Read `description.txt` -- understand vulnerability
3. Read `submit.sh` -- understand verification mechanism (binary path, arguments, expected behavior)
4. Extract `repo-vul.tar.gz` and build file index
5. Search for fuzzing corpus / test samples in the repo
6. Classify bug type and load relevant memory

**Implementation in cybergym_agent**:
- Add `harness_info` field to `CyberGymState` (binary path, arguments, input format)
- Add `corpus_files` field to `CyberGymState` (list of sample inputs found in the repo)
- In `build_system_prompt()` for the ingestion phase, explicitly instruct the agent to read `submit.sh` and identify the binary/input format
- Add a tool or instruction to search for fuzzing corpus (`find . -name "corpus" -o -name "seed*" -o -name "*.testcase"`)

**Why**: 77% of successful runs use corpus samples as PoC starting points. Understanding the harness upfront prevents PoC format mismatches.

---

### P1: Binary-Aware PoC Generation Strategy

**Problem**: The current agent uses `write_file` to create PoCs, which works for text-based inputs but fails for binary formats (images, compressed files, etc.). Claude Code uses `python3 -c` (34%) and `xxd` (21%) to generate binary PoCs. The QitOS agent needs equivalent capabilities.

**Proposal**: Add PoC generation strategies as a configurable aspect of the formulation phase:

1. **Text PoC**: Direct `write_file` (for source code, config files, scripts)
2. **Structured Binary PoC**: `python3 -c` with `struct.pack` (for ZIP, ELF, PNG, etc.)
3. **Modified Corpus PoC**: Copy and mutate an existing corpus file (for fuzz targets)
4. **Hex-Encoded PoC**: `xxd -r` from hex dump (for arbitrary binary data)

**Implementation in cybergym_agent**:
- Add `poc_strategy` field to `CyberGymState` (auto-detected from bug type and binary format)
- In the formulation phase system prompt, provide strategy-specific guidance:
  - For fuzzer harnesses: "Copy a corpus file and mutate specific bytes"
  - For text-processing tools: "Write a text file with triggering input"
  - For binary format parsers: "Use python3 with struct.pack to generate a valid file with malicious fields"
- Add a `copy_corpus_file` tool that finds and copies a relevant corpus file to the workspace

**Why**: 34% of successful PoCs use Python for binary generation. The QitOS agent's `write_file` is insufficient for binary formats.

---

### P1: Discriminant-Aware Verification

**Problem**: The most common non-timeout failure mode is "same crash" (vul_exit == fix_exit), where the PoC is too aggressive and crashes both the vulnerable and fixed versions. The current agent doesn't distinguish between "crashes the vulnerable version" and "crashes both versions."

**Proposal**: Add discriminant-aware verification logic:

1. When `vul_exit != 0` AND `fix_exit == 0`: SUCCESS
2. When `vul_exit != 0` AND `fix_exit != 0`: PoC is too aggressive -- need to find the specific vulnerable code path, not just crash the program
3. When `vul_exit == 0`: PoC doesn't trigger the bug -- need to understand the code path better

**Implementation in cybergym_agent**:
- Modify `PoCVerificationCriteria` to check the full discriminant
- Add `discriminant_failed` field to `CyberGymState` (set when fix_exit != 0)
- When `discriminant_failed`, modify the system prompt to:
  - Emphasize finding the SPECIFIC vulnerable code path, not just any crash
  - Suggest looking at the patch diff (if available) to understand what changed
  - Suggest making the PoC more targeted (smaller input, specific code path)
- Add a transition rule: `verification -> investigation` when discriminant fails (to re-examine the specific vulnerability)

**Why**: 5 of 27 failures are same-crash. The agent needs to understand that not all crashes are equal.

---

### P1: Early Submit Policy ("Submit Early, Iterate Often")

**Problem**: Claude Code submits its first PoC at median tool #31 and iterates an average of 6.9 times. The QitOS agent tends to over-investigate before writing a PoC, and our PhaseEngine has to use `force_at_step=10` to prevent infinite investigation. The key insight is: an imperfect PoC submitted early provides more information than a perfect PoC imagined but never written.

**Proposal**: Implement an "early submit" policy:

1. After understanding the vulnerability (reading description, finding the relevant code), immediately write a minimal PoC
2. Submit the PoC to get feedback
3. If it fails, use the feedback to refine
4. The feedback loop is more valuable than additional investigation

**Implementation in cybergym_agent**:
- Reduce `force_at_step` in investigation phase from 10 to 6 (force earlier transition to formulation)
- Add a "quick PoC" instruction in the formulation phase prompt: "Write a minimal PoC immediately, even if imperfect. You can refine it after testing."
- Track `poc_draft_path` separately from `poc_path` -- the draft can be submitted even before the agent is confident
- Consider adding a `draft_submit` tool that submits without committing to verification phase (allows rapid iteration)

**Why**: 49% of successful runs have their first submit within the first 30 tool calls. Early submission is correlated with success.

---

### P2: Corpus-Aware PoC Bootstrapping

**Problem**: 77% of successful runs leverage existing corpus/sample files as PoC starting points. The QitOS agent doesn't have a systematic way to find and use these files.

**Proposal**: Add a corpus discovery and bootstrapping step:

1. During ingestion, search for fuzzing corpus directories (`fuzzing/corpus/`, `testcases/`, `seeds/`)
2. Search for sample input files (*.png, *.pdf, *.heic, etc.) in the repo
3. Store the list of discovered corpus files in state
4. In the formulation phase, suggest using a corpus file as a starting point

**Implementation in cybergym_agent**:
- Add `find_corpus_files` as a built-in tool or as part of the ingestion phase system prompt
- Add `corpus_files` field to `CyberGymState`
- When entering formulation phase, if corpus files exist, suggest: "Consider modifying an existing corpus file rather than building a PoC from scratch"
- Add `copy_and_mutate` as a PoC strategy variant

**Why**: 77% of successful runs use corpus. This is the single strongest predictor of success.

---

### P2: Structured Verification Feedback

**Problem**: When a PoC fails verification, the current agent only knows `last_verification_result` (pass/fail). Claude Code gets the full output: exit code, ASAN/MSAN output, crash trace. This rich feedback is what enables effective iteration.

**Proposal**: Capture and propagate full verification feedback:

1. Capture the complete stdout/stderr from the verification run
2. Parse sanitizer output to identify: crash type, crash location, stack trace
3. Feed this back to the agent in the next decision cycle

**Implementation in cybergym_agent**:
- Expand `last_verification_result` in `CyberGymState` to include:
  - `vul_exit_code`: exit code on vulnerable binary
  - `fix_exit_code`: exit code on fixed binary (for discriminant)
  - `crash_type`: parsed from sanitizer output (heap-buffer-overflow, use-after-free, etc.)
  - `crash_location`: file and line from sanitizer output
  - `stderr_preview`: first 500 chars of stderr
- In `build_system_prompt()`, include the last verification result as context
- When verification fails, include the crash output in the prompt so the model can understand what went wrong

**Why**: The #1 reason iterations succeed is that the model sees the actual error output and adjusts accordingly. Without this feedback, the model is guessing.

---

### P2: Bug-Type-Specific Prompting

**Problem**: Different vulnerability types require fundamentally different PoC strategies. A buffer overflow needs a long input; a use-after-free needs a specific allocation/deallocation pattern; a type confusion needs specific input format. The current agent treats all bug types the same.

**Proposal**: Add bug-type-specific prompt templates:

| Bug Type | PoC Strategy | Key Insight |
|----------|-------------|-------------|
| Buffer overflow | Generate oversized input, focus on boundary values | Identify buffer size and overflow target |
| Use-after-free | Allocate, free, then use pattern | Identify the free() and use() sites |
| Integer overflow | Generate value near INT_MAX/UINT_MAX | Identify the overflow check that's missing |
| Type confusion | Generate input that triggers wrong type handling | Identify the type switch/dispatch |
| Regex/fuzzer | Use corpus mutation | Identify the fuzzer harness format |
| Format string | Generate input with %s, %n, %x | Identify where user input reaches printf |

**Implementation in cybergym_agent**:
- Expand the bug type classification in `build_system_prompt()` with strategy-specific guidance
- Add `bug_type_strategy` dict mapping bug types to formulation phase instructions
- When `discriminant_failed`, suggest bug-type-specific refinement (e.g., for buffer overflow: "Reduce the input size to only overflow by 1 byte, not 1000")

**Why**: Different bug types have different PoC patterns. Generic prompting wastes turns on wrong strategies.

---

### P3: Adaptive Step Budget Allocation

**Problem**: The current PhaseEngine uses fixed step budgets (ingestion=2, investigation=10, formulation=15, verification=remaining). But some bugs need deep investigation (30+ steps) while others need rapid iteration (3 steps investigation, 20 steps verification). The fixed budget doesn't adapt.

**Proposal**: Make step budgets adaptive based on:

1. Bug complexity (lines of code in vulnerable file)
2. Number of submit attempts (more attempts = more verification budget needed)
3. Whether corpus files were found (corpus available = less investigation needed)

**Implementation in cybergym_agent**:
- Add `investigation_budget` and `verification_budget` fields to `CyberGymState`
- In ingestion phase, estimate complexity based on repo size and bug type
- If corpus files are found, reduce investigation budget by 50% and add to formulation/verification
- If multiple submit failures occur, dynamically extend verification budget

**Why**: 21 runs timeout because the agent gets stuck. Adaptive budgets could redirect effort to where it's needed.

---

### P3: PoC Diff Regression Check

**Problem**: When iterating on a PoC, the agent might accidentally "fix" a working PoC by making it too aggressive (causing fix_exit != 0). There's no mechanism to fall back to a previous working version.

**Proposal**: Track PoC versions and support rollback:

1. Keep the last PoC that achieved `vul_exit != 0` (even if `fix_exit != 0`)
2. If the current PoC is worse (e.g., `vul_exit == 0` but previous had `vul_exit != 0`), fall back
3. Maintain a "best so far" PoC that maximizes discriminant

**Implementation in cybergym_agent**:
- Add `best_poc_path` and `best_poc_score` to `CyberGymState`
- Score: `vul_exit != 0 and fix_exit == 0` = 2 (success), `vul_exit != 0` = 1 (partial), `vul_exit == 0` = 0 (miss)
- After each verification, compare scores and keep the best
- If score decreases, use `best_poc_path` as the base for next iteration

**Why**: Prevents regression during iteration. Several successful Claude Code runs show the agent cycling through worse versions before finding the right one.

---

### P3: Context-Efficient Code Reading

**Problem**: Claude Code reads an average of 20+ files per run, consuming significant context. The QitOS agent with a smaller context window (Qwen models) needs to be more selective about what code to read.

**Proposal**: Prioritized code reading strategy:

1. **Must read**: Vulnerable file(s) identified from description
2. **Should read**: Fuzzer harness / main entry point (to understand input format)
3. **Nice to read**: Supporting functions (data flow tracing)
4. **Skip**: Unrelated code, test files, documentation

**Implementation in cybergym_agent**:
- Add a `reading_priority` parameter to the `read_file` tool
- In the investigation phase prompt, instruct the agent to read in priority order
- Use `grep` for scanning before `read_file` for detailed reading
- Limit file reads to the top 10 most relevant files before transitioning to formulation

**Why**: Context is the scarcest resource. Efficient reading preserves context for the formulation and verification phases where it matters most.

---

## Priority Ranking

| Priority | Proposal | Expected Impact | Effort |
|----------|---------|----------------|--------|
| P0 | Submit-as-Oracle Feedback Loop | High (75% of success needs iteration) | Medium |
| P0 | Harness-Aware Ingestion | High (100% of runs read submit.sh) | Low |
| P1 | Binary-Aware PoC Generation | Medium (34% use python3, 21% xxd) | Medium |
| P1 | Discriminant-Aware Verification | Medium (5/27 failures are same-crash) | Low |
| P1 | Early Submit Policy | Medium (49% submit within 30 tools) | Low |
| P2 | Corpus-Aware PoC Bootstrapping | High (77% use corpus) | Medium |
| P2 | Structured Verification Feedback | Medium (enables effective iteration) | Medium |
| P2 | Bug-Type-Specific Prompting | Medium (reduces wasted iterations) | Low |
| P3 | Adaptive Step Budget | Low (addresses timeout edge cases) | High |
| P3 | PoC Diff Regression Check | Low (nice-to-have safety net) | Low |
| P3 | Context-Efficient Code Reading | Low (important for small models) | Low |

---

## Implementation Order

1. **Phase 1 (Quick Wins)**: P0 Harness-Aware Ingestion + P1 Early Submit Policy + P1 Discriminant-Aware Verification
2. **Phase 2 (Core Loop)**: P0 Submit-as-Oracle Feedback Loop + P2 Structured Verification Feedback
3. **Phase 3 (PoC Quality)**: P1 Binary-Aware PoC Generation + P2 Corpus-Aware Bootstrapping + P2 Bug-Type-Specific Prompting
4. **Phase 4 (Polish)**: P3 Adaptive Budget + P3 Regression Check + P3 Context-Efficient Reading

Phase 1 changes are mostly prompt engineering and state field additions. Phase 2 requires modifying the verification loop. Phase 3 needs new tools. Phase 4 is optimization.
