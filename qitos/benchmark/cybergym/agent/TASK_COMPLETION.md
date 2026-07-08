# Tree-sitter Analysis Task Completion

## Implemented

- Stable expression, symbol, call, constraint, summary, edge, path, candidate, brief, and source-location contracts with JSON schemas and C/C++ queries.
- Immutable structural-graph bootstrap, per-file SQLite persistence, static scope isolation, overload-safe symbol IDs, candidate call graph, reverse paths, SCCs, entrypoints, argument binding, structured substitution, control guards, local slicing, persistent results, brief rendering and paging.
- Automatic PoC Recipe enrichment through every `record_sink_candidate`, full-file crash-isolated sink detectors, target/call-expression normalization, READ focus queries, one-shot deltas, context-budget trimming, and graceful partial results.
- Harness-first input propagation, indexed risk signals, diversified sink-search navigation, Top-3 provisional lead registration, model-confirmed lead upgrades, candidate-neighborhood comparison, and description-anchor downgrading.
- Twenty-seven named Golden scenarios plus runtime, persistence, worker-failure, trigger, READ and observation-lifecycle tests.

## Known gaps

See `LIMITATIONS.md`; uncertain dispatch is explicitly returned as unresolved.

## Verification

Run `PYTHONPATH=../qitos:.. python scripts/benchmark_treesitter_analysis.py`. It covers local `arvo_23764`, `arvo_17986`, and `defending-code-reference-harness`. Golden fixtures exercise complete paths, enrichment, deduplication, paging, and brief-to-path drill-down.

Measured with an 8-second per-pass budget after the sink-navigation cutover:

- `arvo_23764`: 25 files, 248 functions, 1,232 callsites; cold 3.11 s, warm reconstruction 150 ms, Top-5 navigation 3.2 ms/355 estimated tokens.
- `arvo_17986`: the cold bounded pass indexed 60/2,205 files and 1,658 functions in 6.84 s; the continuation reused those files and grew to 92 files/1,978 functions in 7.07 s. Top-5 navigation took 8.7 ms/360 tokens.
- `defending-code-reference-harness`: 4 files, 11 functions, 74 callsites; cold 394 ms, warm reconstruction 6.5 ms, Top-5 navigation 1 ms/299 tokens.

The regression suite contains 135 passing tests. Automatic recipes and navigation briefs are capped at 1,500 estimated tokens, paths at Top-3/depth-6, argument traces at eight steps, and identical candidate/graph fingerprints reuse stable IDs.

## Representative traces

1. `LLVMFuzzerTestOneInput -> parse -> sink`, preserving `n > 8` from an early return.
2. `a -> b -> c`, with three-level formal/actual bindings.
3. `f -> g` through nested `if` guards.
4. `f -> g` through `switch` case/fallthrough routing.
5. Recursive `f -> f` is cycle-bounded and never expanded without limit.

Five enrichment forms are exercised: a direct sink call, a named vulnerable function, an unresolved external call, a brief trimmed to its token budget, and a candidate update that produces a versioned result. Each returns confirmed target evidence, candidate paths or an explicit no-path reason, inferred constraints/dataflow, unresolved IDs, `full_result_id`, and suggested drill-down arguments. The integration test follows `brief.candidate_paths[0].path_id -> get_path_details`, while pagination tests follow `full_result_id -> get_analysis_result(section, offset, limit)`.
