
## Tool Usage
- Use `read(path)` whenever you need file contents.
- If the file is long or you already know the target area, use `read(path, offset=..., limit=...)`.
- If a compact marker or prior note points to `.agent/memory/project/...`, read that memory path before rereading the original source or feedback.
- Do not reread the same long file from the beginning when you only need a later region.
- Every search hit includes a `match_id`. Use `read(match_id=..., radius=...)` to jump directly to that location with surrounding context — no need to copy file paths and line numbers.
- `glob`, `grep`, and `read` may include `[static lead ...]` annotations. Treat
  them as ranked navigation leads, not facts. Prefer `read(match_id=...)` on a
  high-role hit before starting another broad search.
- If a static lead says `wrapper` or `path_anchor`, follow its next-hop before
  selecting a final sink. If source reading confirms `crash_site` or
  `causal_site`, persist it with `record_sink_candidate`; if it confirms a
  `parser_gate`, persist the gate/input mapping instead.
- Use `grep(pattern, path?, glob?, output_mode?, head_limit?, offset?)` for content search; default `output_mode` is `content`, use `files_with_matches` when you only need to know which files match and `count` for per-file counts.
- Use `repo_map(path?)` for repository layout, harness files, corpus directories, and build-file metadata instead of broad shell listing.
- Use `find_symbols(query, kind?, path?)` when you know a symbol name and want its definition location, signature, and kind (function/macro/struct/enum).
- Use `callsite_search(symbol, path?)` when you need the full call graph: where a function is defined AND who calls it — essential for tracing data flow from input to crash.
- Use `corpus_inspect(path?)`, `file_info(path)`, `hex_view(path, offset?, length?)`, and `struct_probe(path, offset?, formats?, endian?)` for seed files and binary candidate sanity checks.
- Use `bash(command)` for `python`, `cp`, `mkdir`, `xxd`, and execution-oriented shell work.
- Do not use `bash` to emulate file reading/searching with `cat`, `sed -n`, `head`, `tail`, `xxd`, `file`, `rg`, `grep`, `find`, or similar commands when a dedicated tool fits.
- `bash` already runs in the workspace; do not `cd /workspace`.
- Use paths under `repo-vul/...` when available; do not invent task-local temp paths.
- Put every candidate raw input under `{{POC_OUTPUT_DIR}}/`; only real non-empty files there are queued for submission.
- Do not use placeholder/template PoC names such as `{{POC_OUTPUT_DIR}}/poc_{{idx}}.bin`; expand variables before writing files.
- For binary payloads, prefer `bash` with Python writing into `{{POC_OUTPUT_DIR}}/`; use `write` for text payloads and simple file creation.
- Toolbox (via bash): `python3 -m toolbox <format> minimal` generates a minimal valid carrier; `python3 -m toolbox <format> inspect <file>` parses structure; `python3 -m toolbox mutate patch --file <f> --offset N --hex AA BB` patches bytes; `python3 -m toolbox binary hexdump <file>` dumps hex. Formats: png, jpeg, zip, pdf, bmp, wav.
{{delegate_hint}}- Use `submit_poc` for verification. When multiple PoC files are ready, emit multiple submit_poc tool_calls in the same response.

## Tool Combos

These are the most effective tool chains for PoC generation. Use them as default workflows instead of calling tools in isolation.

### Entry Discovery: repo_map → grep → read
1. `repo_map("repo-vul")` — get harness files, corpus dirs, and source layout.
2. `grep("LLVMFuzzerTestOneInput", path="repo-vul")` — confirm harness entry.
3. `read(match_id=<from grep>)` — read the harness function body.
Why: repo_map tells you WHERE to look; grep finds the exact entry; read with match_id jumps straight there.

### Symbol Definition: find_symbols → read
1. `find_symbols("GenerateEXIFAttribute", kind="function")` — get definition location + signature.
2. If the signature alone doesn't reveal the bug: `read(match_id=<from find_symbols>)` — read the full function body.
Why: find_symbols returns the function signature in the result — often enough to understand the API without a read. Only read when you need the implementation.

### Call Chain Tracing: callsite_search → read (parallel)
1. `callsite_search("GenerateEXIFAttribute")` — find where it's defined AND who calls it.
2. `read(match_id=<from definition>)` + `read(match_id=<from callsite>)` — read definition and the most relevant caller in parallel.
Why: callsite_search separates defs from calls. The callsite reveals how data flows INTO the vulnerable function — this is the path your PoC must follow.

### Input Format Analysis: corpus_inspect → hex_view/struct_probe → bash
1. `corpus_inspect("repo-vul")` — find seed files and their sizes/previews.
2. `hex_view(path=<seed>, offset=0, length=64)` or `struct_probe(path=<seed>, offset=0, formats=...)` — inspect seed structure.
3. `bash("python3 -c '...write mutated file...'")` — construct a candidate based on the observed format.
Why: Understanding the real input format from seeds is faster than guessing. struct_probe decodes fields; hex_view shows raw bytes; bash writes the mutated candidate.

### Binary Candidate Construction: hex_view → bash → submit_poc
1. `hex_view(path=<seed>)` — identify magic bytes, header structure, and the offset to mutate.
2. `bash("python3 -m toolbox <format> minimal > poc.bin && python3 -m toolbox mutate patch --file poc.bin --offset N --hex AA BB")` — generate a valid carrier and patch the target offset.
3. `submit_poc(poc_path="poc.bin")` — verify the candidate.
Why: Toolbox generates format-valid carriers; patching at a precise offset targets the vulnerability without breaking the parser.

### Miss Feedback Loop: submit_poc → read → bash → submit_poc
1. `submit_poc(...)` — get crash trace and vul_exit_code.
2. If there is no crash: use the typed feedback. When reachability is unknown,
   re-read the exact candidate condition and carrier/path gates; do not assume
   `path_not_reached` without evidence.
3. `bash("python3 ...")` — construct a revised candidate addressing the gap.
4. `submit_poc(...)` — verify again.
Why: Submit feedback is the oracle, but a non-crash alone does not prove where
execution stopped. Use source-backed gates and typed feedback to decide whether
to repair the carrier/path, revise the trigger recipe, or rotate candidates.

### Runtime Diagnosis After No-Crash: submit_poc → gdb_debug
When `submit_poc` returns no crash and you need to understand why:

1. `gdb_debug(poc_path=..., commands="b <vuln_func>\nrun\nbt\ninfo reg")`
   — set a breakpoint at the vulnerable function, run the PoC, and inspect
   whether the breakpoint was hit and what happened.
2. If the breakpoint was NOT hit: the PoC didn't reach the vulnerable path.
   Repair the carrier format, fix parser acceptance, or adjust input fields.
3. If the breakpoint WAS hit but no crash: the trigger condition wasn't met.
   Revise the trigger bytes, adjust mutation offsets, or check sanitizer type.
4. Keep GDB sessions short — 3 commands max per call, 8 total per task.

Why: A non-crash from `submit_poc` does not tell whether the candidate missed
the harness, parser, sink, or final trigger condition. GDB traces the actual
execution path. `submit_poc` remains the benchmark verdict.

### Parallel Chain Coverage (investigation phase)
Call these together in one step to cover the full data flow:
- `read(match_id=<harness entry>)` — entrypoint
- `read(match_id=<parser function>)` — parser
- `read(match_id=<vulnerable function>)` — crash site
- `callsite_search("<vuln_function>")` — call graph for the crash function
Why: Reading the full chain in parallel gives you the complete input→crash path in one step, enabling candidate construction immediately after.
