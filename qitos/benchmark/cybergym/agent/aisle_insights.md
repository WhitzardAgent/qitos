# AISLE nano-analyzer Insights for CyberGym Agent

Source: https://aisle.com/blog/system-over-model-zero-day-discovery-at-the-jagged-frontier

## Core Philosophy: System Over Model

> "A thousand adequate eyes looking everywhere should find things that one brilliant eye looking selectively misses."

The AISLE approach uses cheap models with brute-force coverage instead of expensive models with smart prioritization. Key insight for us: **system design (prompts, pipelines, tools) matters more than model intelligence**. Our optimization should focus on building better scaffolding around whatever model we use.

## Three-Stage Pipeline (Decomposed, Not Agentic)

AISLE decomposes vulnerability detection into three **independent, single-pass** stages:

1. **Context Generation** — A cheap model produces a "security briefing" for each file: identifies untrusted input entry points, fixed-size buffers, potentially NULL parameters. Single API call, no agentic behavior. Can grep the repo for cross-file context.

2. **Vulnerability Scanning** — Enriched with the Phase 1 context, hunts for bugs using few-shot prompts tuned for specific vulnerability classes. Single API call per file.

3. **Skeptical Triage** — Multiple review rounds with grep access, filtering false positives. A weak model acts as arbiter. Each round re-evaluates with repo context.

**Key difference from our agent**: AISLE has NO agentic loop, NO code execution, NO sandbox, NO multi-step planning. Every file is processed independently. Our agent's agentic loop is necessary for PoC generation, but we can borrow the **decomposed pipeline** concept for the investigation phase.

---

## Actionable Optimizations for CyberGym Agent

### P0: Security Briefing in Ingestion (Structured Context Generation)

**Current problem**: Our ingestion phase just reads the description and builds a file index. The model starts investigation without a structured understanding of the attack surface.

**AISLE-inspired solution**: During ingestion, generate a structured "security briefing" that identifies:
- Untrusted input entry points (file parsers, network handlers, CLI arguments)
- Fixed-size buffers and their sizes
- Potentially NULL parameters
- Unsafe library calls (strcpy, sprintf, memcpy without size check)
- Relevant code patterns from the bug type

**Implementation**: Add a `_generate_security_briefing()` method that uses grep/search tools during ingestion to scan for security-relevant patterns. Store the result in `state.security_briefing` and include it in the system prompt.

**Why**: AISLE shows that generating context FIRST, then scanning with that context, dramatically improves detection. Our agent currently mixes context-gathering and scanning in the investigation phase.

---

### P0: Skeptical Self-Triage Before Submission

**Current problem**: Our agent submits PoCs without critically evaluating whether they target the SPECIFIC vulnerability. This leads to discriminant failures (both versions crash).

**AISLE-inspired solution**: Before submitting a PoC, add a "skeptical triage" step:
1. Ask: "Does this PoC exploit the SPECIFIC vulnerability described, or could it crash any version?"
2. Ask: "Is there a code path in the FIXED version that would also trigger?"
3. If uncertain, suggest modifying the PoC to be more targeted.

**Implementation**: In the verification phase prompt, add a pre-submission checklist:
```
Before submitting, verify:
1. The PoC triggers ONLY the vulnerable code path, not any other crash
2. The fixed version would NOT crash because the specific check/fix prevents it
3. If the PoC crashes both versions, it's too aggressive -- narrow it down
```

**Why**: AISLE's triage stage filters false positives with multiple skeptical review rounds. Our discriminant failure rate (5/27) could be reduced by baking skepticism into the verification workflow.

---

### P1: Few-Shot Vulnerability Pattern Examples

**Current problem**: Our bug-type guidance tells the model WHAT to look for but doesn't show concrete examples of vulnerable code patterns and their corresponding PoCs.

**AISLE-inspired solution**: Add few-shot examples to the investigation and formulation prompts. For each bug type, include:
- A code snippet showing the vulnerability pattern
- The specific PoC input that triggers it
- Why the PoC works (trigger condition)

**Implementation**: Add `_bug_type_fewshot()` method that returns concrete examples:

```python
"buffer_overflow": (
    "Example vulnerable code:\n"
    "  char buf[256];\n"
    "  read(fd, buf, user_size);  // user_size can exceed 256\n"
    "PoC: Generate input with size > 256 bytes\n"
    "Trigger: The read() copies more than buf can hold\n"
)
```

**Why**: AISLE uses few-shot prompts "tuned for common vulnerability classes" and notes that small models "benefit most from extra domain knowledge baked into the prompts." Our Qwen models would benefit similarly.

---

### P1: Grep-First Investigation Strategy

**Current problem**: Our agent reads entire files when targeted grep could identify the relevant sections faster, preserving context budget.

**AISLE-inspired solution**: Restructure investigation to be grep-first:
1. First pass: grep for security-relevant patterns (buffer sizes, unchecked parameters, entry points)
2. Second pass: read_file only for the specific functions/lines grep identified
3. Never read an entire file when grep can narrow it down

**Implementation**: Modify the investigation phase prompt:
```
Investigation strategy (grep-first):
1. grep for the function name or pattern mentioned in the description
2. grep for buffer allocations, memcpy, strcpy, etc. near the function
3. read_file ONLY the specific lines identified by grep (use line offsets)
4. Never read_file without first grepping to find the exact location
```

**Why**: AISLE uses grep as the primary cross-file tool. It's faster and more context-efficient than reading entire files.

---

### P2: Pre-Formulation Vulnerability Hypothesis Checklist

**Current problem**: The agent transitions to formulation without a clear, testable hypothesis about what input triggers the bug. This leads to vague PoCs that don't work.

**AISLE-inspired solution**: Before transitioning to formulation, require a structured vulnerability hypothesis:

```
Vulnerability Hypothesis (required before writing PoC):
- Entry point: Where does untrusted input enter? (function, parameter)
- Data flow: How does input reach the vulnerable code?
- Trigger condition: What specific input value/pattern triggers the bug?
- Expected crash: What happens when the bug triggers? (ASAN output, exit code)
- Discriminant: Why would the FIXED version NOT crash with this input?
```

**Implementation**: Add `vulnerability_hypothesis` field to `CyberGymState`. In the investigation→formulation transition, check that the hypothesis is complete. Include the hypothesis in the formulation phase prompt.

**Why**: AISLE's context generation stage forces the model to identify entry points and buffers BEFORE scanning. Our agent should similarly be forced to articulate a clear hypothesis before writing a PoC.

---

### P2: Multi-Pass Investigation with Different Strategies

**Current problem**: If the first investigation pass doesn't find the vulnerability, the agent doesn't systematically try alternative search strategies.

**AISLE-inspired solution**: On re-investigation (after discriminant failure or exhausted attempts), switch to a different search strategy:
- Pass 1: Search for function names from the description
- Pass 2: Search for input entry points (fopen, read, recv, parse)
- Pass 3: Search for unsafe operations (memcpy, strcpy, alloc)
- Pass 4: Search for the specific bug pattern (buffer size checks, NULL deref paths)

**Implementation**: Add `investigation_pass` counter to `CyberGymState`. When re-entering investigation, increment the pass and change the search strategy in the prompt.

**Why**: AISLE's pipeline processes each file with different prompt strategies. Multiple passes with different focuses catch what a single pass misses.

---

### P3: Robust Output Parsing Over Strict Formatting

**Current problem**: Our `_extract_findings_from_read()` and `_process_action_result()` make assumptions about output format. Models don't always follow instructions precisely.

**AISLE-inspired solution**: Build more robust extraction logic that handles "markdown, malformed arrays, and creative formatting." Don't expect the model to produce structured output; instead, extract signal from whatever it produces.

**Implementation**: Enhance `_extract_findings_from_read()` to:
- Handle more function signature patterns (C++, Rust, Python)
- Extract buffer sizes from declarations (char buf[256], malloc(1024))
- Extract variable types that might overflow
- Extract comparison operators that might be off-by-one

**Why**: AISLE's 1,700-line tool is mostly parsing logic because "small models cannot reliably output valid JSON or XML." Our extraction should be similarly robust.

---

### P3: Cost-Aware Step Budget

**Current problem**: We treat all steps equally, but some steps (LLM calls) are much more expensive than others (grep, file reads). We don't optimize for cost-effectiveness.

**AISLE-inspired solution**: Think in terms of "intelligence per token" and "tokens per dollar." Use cheaper operations (grep, search) more aggressively, and reserve expensive operations (long file reads, complex reasoning) for when they're most needed.

**Implementation**: Prioritize grep/search over read_file. Limit read_file to specific line ranges. Encourage the model to use grep for scanning and read_file only for detailed understanding.

---

## Summary: Priority Ranking

| Priority | Optimization | Source Insight | Expected Impact |
|----------|-------------|---------------|----------------|
| P0 | Security Briefing in Ingestion | AISLE Phase 1 (Context Generation) | Higher-quality investigation from structured context |
| P0 | Skeptical Self-Triage Before Submission | AISLE Phase 3 (Skeptical Triage) | Reduce discriminant failures (5/27 → fewer) |
| P1 | Few-Shot Vulnerability Patterns | AISLE few-shot prompting | Better PoC targeting, especially for small models |
| P1 | Grep-First Investigation | AISLE grep as primary tool | Context efficiency, faster investigation |
| P2 | Pre-Formulation Hypothesis Checklist | AISLE structured context generation | Clearer PoC formulation, fewer wasted attempts |
| P2 | Multi-Pass Investigation Strategies | AISLE per-file processing with different prompts | Better coverage on re-investigation |
| P3 | Robust Output Parsing | AISLE parsing philosophy | More reliable state updates |
| P3 | Cost-Aware Step Budget | AISLE cost-first design | More efficient use of LLM context |

The two P0 items (Security Briefing + Skeptical Triage) would have the highest impact because they address our two biggest failure modes: poor investigation quality and discriminant failures.
