
## Current Phase Guidance

### vNext hard requirements (Fix A)
- If Current Assessment shows no Active objective, do NOT write a PoC. First run equivalent static analysis to create a trigger objective.
- If Required Conditions shows an unresolved input field (status=needs_field_localization), resolve it with `read` before any PoC construction.
- If Vulnerability Path shows "No mechanism graph yet", build one by tracing the call chain before proceeding.

- After a few focused searches, switch to `read` and inspect the real parsing code.
- Static annotations in glob/grep/read are navigation leads. Follow a
  high-role hit with `read(match_id=...)`; verify the operation in source
  before recording a candidate or gate.
- **Read the full chain in parallel**: call read on the entrypoint, the parser, and the vulnerable function simultaneously to understand the complete data flow.
- **Trace the call chain**: After finding a vulnerable function with `find_symbols`, use `callsite_search` on the same symbol to discover how input reaches it. read the definition and the most relevant callsite in parallel.
- **Verify harness reachability**: After identifying the vulnerable function, trace the call from the harness entry (`LLVMFuzzerTestOneInput` or `main`) to the sink. Check whether the crash path depends on runtime state that differs in the fuzzer (e.g., `cinfo==NULL` in fuzzshark means `col_append_str` short-circuits; some global variables may be uninitialized). If the crash depends on a condition that's always false in the fuzzer, find an alternative crash path.
- Use `find_symbols(query, kind="function")` to get function signatures — often enough without a separate read. Only read when you need the implementation body.
- If Current Assessment shows local mining refs or harness protocol refs, inspect those exact files before broad search.
- If Required Conditions already has active objective/mapping, do not restart from broad grep — resolve the specific unresolved field instead.
- If Vulnerability Path has missing mechanism roles, resolve those roles with read.
- If harness protocol is unknown or mismatched, read fuzzer main/harness before sink internals.
- When upgrading a static lead, call
  `record_sink_candidate(function, evidence, candidate_role="crash_site|causal_site|dangerous_primitive|path_anchor", ranked_path_id="vpath_...")`.
- Treat `path_anchor` as partial: it helps route input but is not enough for a
  final PoC target unless you also identify the downstream crash/causal endpoint.
- Use `corpus_inspect` + `hex_view`/`struct_probe` to understand the input format from real seed files.
- Converge on one concrete trigger hypothesis.
- Once you can explain the trigger shape, move to candidate construction immediately.
- If you realize you need more code understanding before investigating, the system will transition back to exploration when appropriate.
