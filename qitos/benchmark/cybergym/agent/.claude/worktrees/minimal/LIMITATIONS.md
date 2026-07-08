# Tree-sitter Analysis Limitations

- Virtual dispatch, template instantiation, macro expansion, heap alias analysis, concurrency, and whole-program reachability proofs are intentionally unsupported.
- Complex function pointers and inferred C++ receiver types remain candidate or unresolved results.
- Local slicing follows lexical reaching definitions and does not build full SSA.
- Loops are summarized without unrolling; damaged syntax yields partial data.
- Confidence describes syntactic evidence quality, not exploitability or attacker control.
- A ten-second cold bootstrap can yield `PARTIAL_INDEX` for very large repositories. Target and READ operations fill relevant missing files, but the service does not claim whole-repository coverage until `GRAPH_READY`.
- READ analysis uses the complete indexed file, while the model sees only its selected snippet. Files above the configured size limit remain explicitly partial.
- Navigation scores estimate inspection value, not exploitability or ground truth. The model must READ and explicitly confirm a provisional lead.
- Input propagation is bounded and field-insensitive. Heap aliases and transformations hidden behind unresolved indirect calls may leave provenance partial.
