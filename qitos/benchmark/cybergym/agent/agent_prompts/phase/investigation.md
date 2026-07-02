
## Current Phase Guidance
- After a few focused searches, switch to `READ` and inspect the real parsing code.
- **Read the full chain in parallel**: call READ on the entrypoint, the parser, and the vulnerable function simultaneously to understand the complete data flow.
- **Trace the call chain**: After finding a vulnerable function with `FindSymbols`, use `CallsiteSearch` on the same symbol to discover how input reaches it. READ the definition and the most relevant callsite in parallel.
- **Verify harness reachability**: After identifying the vulnerable function, trace the call from the harness entry (`LLVMFuzzerTestOneInput` or `main`) to the sink. Check whether the crash path depends on runtime state that differs in the fuzzer (e.g., `cinfo==NULL` in fuzzshark means `col_append_str` short-circuits; some global variables may be uninitialized). If the crash depends on a condition that's always false in the fuzzer, find an alternative crash path.
- Use `FindSymbols(query, kind="function")` to get function signatures — often enough without a separate READ. Only READ when you need the implementation body.
- Use `summarize_function(symbol_id)` to quickly understand a function without reading its full source.
- Use `trace_value(function, line, expression)` to trace where a parameter value originates across functions.
- Use `extract_constraints(function, target_line)` to get static-analysis constraints at a callsite.
- Use `explain_path(path_id)` for a readable summary of an interprocedural path (path IDs appear in Suggested Constraints and Interprocedural Analysis).
- Use `CorpusInspect` + `HexView`/`StructProbe` to understand the input format from real seed files.
- Converge on one concrete trigger hypothesis.
- Once you can explain the trigger shape, move to candidate construction immediately.
- If you realize you need more code understanding before investigating, use `switch_phase(target_phase="exploration", reason="need to re-read X to understand Y")`.
