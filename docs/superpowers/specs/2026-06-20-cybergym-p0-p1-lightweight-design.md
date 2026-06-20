# CyberGym Agent P0/P1 Lightweight Upgrade Design

Date: 2026-06-20
Status: Design approved, pending written spec review
Scope: Implement only P0.1, P0.2, P0.4, and P1.1 in a lightweight, non-architectural-expansion way

## 1. Goal

Improve the current `CyberGymAgent`'s CyberGym performance by strengthening four information pipelines without making the agent materially heavier:

1. **P0.1 Candidate provenance/schema strengthening**
2. **P0.2 Structured task-spec extraction**
3. **P0.4 Structured failure taxonomy / failure records**
4. **P1.1 Stronger repo-map / evidence ranking**

The design explicitly avoids runtime architecture expansion. We are not adding worker runtimes, a judge runtime, a cleanroom verifier, or a new orchestration layer.

## 2. Primary constraint

The upgraded agent must remain lightweight.

That means:

- no new heavy dependencies such as tree-sitter or ctags
- no new benchmark-time multi-agent orchestration layer
- no required extra LLM round-trip on every task
- no prompt bloat from dumping full structured objects into model-visible context
- no rewiring of the QitOS `AgentModule + Engine` execution model

The intended improvement mode is:

- better early task interpretation
- better repo targeting
- clearer candidate provenance
- better structured use of submit feedback
- fewer repeated low-value attempts

## 3. Out of scope

This design does **not** implement:

- P0.3 verifier cleanroom execution
- P1.2 dynamic focus partitioner
- P1.3 strategy-specific runtime workers
- P1.4 per-agent token budget tables
- P1.5 serial root-cause judge
- a new candidate execution contract such as per-candidate `run_command` / `submit_command`
- changes to `qitos.core` or `qitos.engine` architecture

## 4. Current-state summary

The current system already has useful but incomplete structure:

- `CyberGymAgent` is the main policy module and owns bootstrap, prompting, candidate generation, feedback processing, and reducer control.
- `CandidateRecord`, `FeedbackRecord`, and `ArtifactStore` already exist, but their roles are only partially aligned.
- task parsing exists today, but is spread across `init_state()`, repo bootstrap, harness parsing, and heuristic description parsing.
- repo evidence bootstrap exists, but is mostly path-heuristic and fixed-family oriented.
- submit feedback already records structured facts, but the failure taxonomy exposed to strategy logic is still coarse.

This makes the right upgrade path a **tightening of existing data structures and heuristics**, not a runtime redesign.

## 5. Target files

### New file

- `qitos/benchmark/cybergym/agent/task_spec.py`

### Modified files

- `qitos/benchmark/cybergym/agent/state.py`
- `qitos/benchmark/cybergym/agent/family_runtime.py`
- `qitos/benchmark/cybergym/agent/evidence_selector.py`
- `qitos/benchmark/cybergym/agent/subagent_runtime.py`
- `qitos/benchmark/cybergym/agent/agent.py`

### Only touch if proven necessary

- `qitos/benchmark/cybergym/agent/adapter.py`
- `qitos/benchmark/cybergym/agent/submit_tool.py`

## 6. Design by feature

---

### 6.1 P0.2 Structured task-spec extraction

#### Problem

Current task understanding is too distributed and too heuristic. The agent extracts:

- CVE ID
- bug type
- affected component
- harness info
- corpus hints

but does not produce a compact, explicit task-spec summary that can be reused consistently by repo bootstrap, evidence ranking, and candidate construction.

#### Design

Add a new module:

- `task_spec.py`

This module will expose a lightweight function such as:

- `build_task_spec(...) -> dict`

The output is a **flat, lightweight summary dict**, not a new heavy runtime object.

It will have two phases:

1. **Deterministic extraction first**
   - CVE / OSS-Fuzz style identifiers
   - sanitizer and crash-signal hints
   - file-extension / input-format hints
   - symbol-looking tokens
   - source-file-looking mentions
   - obvious vulnerability-class hints
2. **Optional low-cost LLM completion only when confidence is low**
   - only one extra call at most
   - strict JSON response
   - only used when key fields are missing or confidence is below threshold

#### State fields to add

Add only model-useful, flat fields to `CyberGymState`:

- `vulnerability_class: str`
- `expected_signal: str`
- `input_vector_hints: List[str]`
- `likely_entrypoints: List[str]`
- `likely_fuzz_targets: List[str]`
- `source_files_mentioned: List[str]`
- `symbols_mentioned: List[str]`
- `task_spec_confidence: float`

#### Integration point

Integrate in `CyberGymAgent.init_state()` after current description/harness/repo bootstrap inputs are available, not in `adapter.py`.

That preserves:

- deterministic `Task` construction in `adapter.py`
- a single strategy-side source of truth in the agent
- compatibility with current init-state flow

#### Model visibility

Do **not** inject the full task-spec dict into the prompt.

Only expose a short summary in `prepare()` / observation packet, such as:

- expected signal
- input-vector hints
- likely entrypoints (top few)
- task-spec confidence if low

#### Expected benefit

- more accurate early search direction
- better family bootstrap ordering
- better repo evidence ranking
- fewer wasted broad reads and invalid first candidates

---

### 6.2 P1.1 Repo-map / evidence ranking enhancement

#### Problem

Current `bootstrap_evidence_index()` is useful but lightweight to a fault. It mostly relies on path names and fixed suffix classes. It does not rank repo evidence strongly enough using task semantics.

#### Design

Keep the current `evidence_index` container, but enrich it instead of introducing a second indexing subsystem.

Enhance `bootstrap_evidence_index()` to derive:

- `parser_paths`
- `seed_paths`
- `field_paths`
- `build_paths`
- `fuzz_target_paths`
- `sample_paths`
- `language_hints`
- `ranked_paths`
- `repo_profile_summary`

The ranking function should be lightweight and combine:

- path-name heuristics
- task-spec `source_files_mentioned`
- task-spec `symbols_mentioned`
- task-spec `input_vector_hints`
- common fuzz/build/sample naming patterns
- penalty for vendor/generated/third-party directories

Allow a small amount of file-content sniffing only for top candidates, for example:

- fuzz macros / harness markers
- build-file markers
- parser-like API names

#### Integration point

Keep current control flow:

- `_ensure_family_bootstrap()` refreshes `evidence_index`
- `_refresh_durable_project_memory()` keeps only trimmed summaries
- `select_family_evidence()` consumes the enriched index

#### Model visibility

The model should only see:

- short repo profile summary
- top-ranked relevant paths
- family-specific evidence slices

It should **not** receive the full index.

#### Expected benefit

- faster convergence on real parser/harness/sample paths
- better use of task description clues
- fewer irrelevant reads in large repos

---

### 6.3 P0.1 Candidate schema / provenance strengthening

#### Problem

`CandidateRecord` already exists, but candidate identity and provenance are under-specified. In particular, current `content_fingerprint` semantics are overloaded:

- delegate-generated candidates use a logical mutation fingerprint
- direct candidates may use file/path-derived fingerprints

This ambiguity weakens dedupe, tracing, and post-submit interpretation.

#### Design

Strengthen `CandidateRecord`, but keep it lightweight.

Add provenance-oriented fields only, such as:

- `producer_agent: str = ""`
- `created_at: str = ""`
- `artifact_ref: str = ""`
- `hypothesis_ref: str = ""`
- `fingerprint_mode: str = ""`
- `artifact_sha256: str = ""`

Do **not** turn candidates into full execution contracts.

Specifically out of scope for this round:

- `run_command`
- `submit_command`
- a separate `CandidateArtifactStore` runtime layer

#### Identity clarification

Standardize candidate identity into two lanes:

1. **logical candidate fingerprint**
   - derived from generation inputs / mutation summary / family
2. **artifact SHA / file fingerprint**
   - derived from actual PoC file content when available

Keep both when useful, but do not overload one field for both meanings without marking mode.

#### Integration points

- `subagent_runtime.py`
  - accept optional provenance fields in candidate JSON
- `agent.py`
  - when constructing delegate candidates, fill defaults for provenance fields
  - when constructing direct candidates, set provenance fields consistently
  - when correlating submit results back to candidates, prefer explicit provenance when available

#### Model visibility

These fields are mainly for runtime correctness and debugability.

The model should not see raw full provenance objects unless a short, targeted summary is needed.

#### Expected benefit

- more reliable dedupe
- more explainable candidate lineage
- better feedback correlation
- safer future extension path without current runtime expansion

Direct benchmark gain is expected to be modest but positive via reduced confusion in candidate handling.

---

### 6.4 P0.4 Failure taxonomy / FailureRecord

#### Problem

The system already has `FeedbackRecord` and coarse submit verdicts, but it lacks a clean, internal-only failure layer that strategy code can use consistently without over-relying on free-form strings.

#### Design

Keep `FeedbackRecord` as the canonical raw submit-feedback record.

Add:

- `FailureType` enum
- `FailureRecord` dataclass

`FailureRecord` is a derived structure, not a replacement for `FeedbackRecord`.

#### First-phase failure taxonomy

Only include types that current evidence can support reliably:

- `SUBMISSION_ERROR`
- `NO_TRIGGER`
- `VUL_ONLY_TRIGGERED`
- `REJECTED_AFTER_TRIGGER`
- `TIMEOUT`
- `OOM`
- `BOTH_SIDES_CRASH` (internal-only)
- `UNKNOWN`

Do **not** include fragile or overly semantic categories in the first pass, such as:

- `NO_TARGET_CODE_REACHED`
- `CONTEXT_INSUFFICIENT`
- `DUPLICATE_ROOT_CAUSE`
- `WRONG_EXECUTABLE`

#### Internal vs model-visible split

Important rule:

- fix-side-sensitive classifications may exist internally
- only safe, coarse summaries reach the model

That preserves the current anti-leakage design.

#### Integration points

- derive `FailureRecord` after append/processing of submit feedback
- store lightweight `failure_history` in state
- distill short failure summaries into existing durable feedback memory pathways
- optionally expose only the latest 1-2 compact failure summaries in observation packets

#### Expected benefit

- cleaner failure memory
- less repeated blind retrying
- more consistent strategy nudges after submit miss/reject
- better analytics and regression debugging

Direct benchmark lift is likely moderate and primarily comes from reducing repeated non-productive retries.

## 7. Prompt and observation policy

To avoid making the agent heavier, new structured data is exposed to the model under a strict summary policy.

### The model may see

- short task-spec summary
- small repo-profile summary
- top-ranked evidence hints
- latest compact failure summary

### The model should not see by default

- full task-spec object
- full evidence index
- full candidate provenance records
- full failure history
- internal fix-side-sensitive taxonomy details

## 8. Implementation order

1. **TaskSpec**
2. **Repo-map / evidence ranking**
3. **Candidate provenance strengthening**
4. **Failure taxonomy**

This order minimizes risk:

- task semantics become available first
- repo ranking can then consume task semantics
- candidate provenance changes happen after upstream signal quality improves
- failure taxonomy comes last as a derived layer

## 9. Testing strategy

### Unit / focused regression coverage

Add or update tests for:

- deterministic task-spec extraction
- low-confidence fallback gating
- repo evidence ranking and path discovery
- candidate fingerprint mode consistency
- failure-type derivation from submit results
- non-leakage of internal-only failure details into agent-facing summaries

### Existing regression safety

Run existing CyberGym / engine tests to ensure:

- no breakage in submit-result processing
- no breakage in current candidate queue / ready queue flow
- no fix-side leakage regression
- no prompt shape explosion

### Lightweight behavior validation

After code changes, validate on a small representative task set:

- time-to-first-candidate
- repeated no-trigger patterns
- harness/corpus usage
- breadth of irrelevant reading

## 10. Risks and mitigations

### Risk: extra parsing call increases latency
Mitigation:
- deterministic parse first
- LLM fallback only under low confidence
- one call maximum

### Risk: repo scan becomes too expensive
Mitigation:
- path heuristics first
- content sniffing only on small top candidate set
- cap ranked outputs

### Risk: candidate provenance expansion breaks queue/dedupe
Mitigation:
- preserve current queue contracts
- add fields without changing control flow shape
- keep fingerprint semantics explicit

### Risk: failure taxonomy becomes too rigid
Mitigation:
- make it advisory/internal first
- do not hardwire aggressive strategy transitions from taxonomy alone

## 11. Acceptance criteria

This design is successful if:

1. The runtime architecture remains single-main-agent and QitOS-compatible.
2. No heavy dependency or new orchestration layer is introduced.
3. `prepare()` / model-visible context stays compact.
4. Task interpretation becomes more explicit and reusable.
5. Repo evidence targeting becomes more relevant.
6. Candidate identity/provenance becomes less ambiguous.
7. Failure summaries become more structured without leaking sensitive verifier details.
8. The resulting agent is more directed, not more verbose or slower by construction.

## 12. Recommendation

Proceed with implementation exactly within this bounded scope.

If the benchmark results improve, the next candidate extension points are:

- dynamic focus partitioning
- one strategy-specific worker prototype
- later-stage root-cause dedupe

But those should only be considered after measuring the effect of this lightweight upgrade set.