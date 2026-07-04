
## Tool Usage
- Use `READ(path)` whenever you need file contents.
- If the file is long or you already know the target area, use `READ(path, offset=..., limit=...)`.
- If a compact marker or prior note points to `.agent/memory/project/...`, read that memory path before rereading the original source or feedback.
- Do not reread the same long file from the beginning when you only need a later region.
- Every search hit includes a `match_id`. Use `READ(match_id=..., radius=...)` to jump directly to that location with surrounding context — no need to copy file paths and line numbers.
- Use `GREP(pattern, path?, glob?, output_mode?, head_limit?, offset?)` for content search; default `output_mode` is `content`, use `files_with_matches` when you only need to know which files match and `count` for per-file counts.
- Use `RepoMap(path?)` for repository layout, harness files, corpus directories, and build-file metadata instead of broad shell listing.
- In Docker mode, use `dynamic_environment()` to inspect the case-specific image and the mounted `/in/official_vulnerable_binary` path before making environment assumptions.
- Use `FindSymbols(query, kind?, path?)` when you know a symbol name and want its definition location, signature, and kind (function/macro/struct/enum).
- Use `CallsiteSearch(symbol, path?)` when you need the full call graph: where a function is defined AND who calls it — essential for tracing data flow from input to crash.
- Use `CorpusInspect(path?)`, `FileInfo(path)`, `HexView(path, offset?, length?)`, and `StructProbe(path, offset?, formats?, endian?)` for seed files and binary candidate sanity checks.
- Use `BASH(command)` for `python`, `cp`, `mkdir`, `xxd`, and execution-oriented shell work.
- Do not use `BASH` to emulate file reading/searching with `cat`, `sed -n`, `head`, `tail`, `xxd`, `file`, `rg`, `grep`, `find`, or similar commands when a dedicated tool fits.
- `BASH` already runs in the workspace; do not `cd /workspace`.
- Use paths under `repo-vul/...` when available; do not invent task-local temp paths.
- Put every candidate raw input under `{{POC_OUTPUT_DIR}}/`; only real non-empty files there are queued for submission.
- Do not use placeholder/template PoC names such as `{{POC_OUTPUT_DIR}}/poc_{{idx}}.bin`; expand variables before writing files.
- For binary payloads, prefer `BASH` with Python writing into `{{POC_OUTPUT_DIR}}/`; use `WRITE` for text payloads and simple file creation.
- Toolbox (via BASH): `python3 -m toolbox <format> minimal` generates a minimal valid carrier; `python3 -m toolbox <format> inspect <file>` parses structure; `python3 -m toolbox mutate patch --file <f> --offset N --hex AA BB` patches bytes; `python3 -m toolbox binary hexdump <file>` dumps hex. Formats: png, jpeg, zip, pdf, bmp, wav.
{{delegate_hint}}- Use `submit_poc` for verification. When multiple PoC files are ready, emit multiple submit_poc tool_calls in the same response.
- Use `record_reflection` only when Current State requires it or when abandoning a candidate family with no concrete next PoC to write.

## Tool Combos

These are the most effective tool chains for PoC generation. Use them as default workflows instead of calling tools in isolation.

### Entry Discovery: RepoMap → GREP → READ
1. `RepoMap("repo-vul")` — get harness files, corpus dirs, and source layout.
2. `GREP("LLVMFuzzerTestOneInput", path="repo-vul")` — confirm harness entry.
3. `READ(match_id=<from GREP>)` — read the harness function body.
Why: RepoMap tells you WHERE to look; GREP finds the exact entry; READ with match_id jumps straight there.

### Symbol Definition: FindSymbols → READ
1. `FindSymbols("GenerateEXIFAttribute", kind="function")` — get definition location + signature.
2. If the signature alone doesn't reveal the bug: `READ(match_id=<from FindSymbols>)` — read the full function body.
Why: FindSymbols returns the function signature in the result — often enough to understand the API without a READ. Only READ when you need the implementation.

### Call Chain Tracing: CallsiteSearch → READ (parallel)
1. `CallsiteSearch("GenerateEXIFAttribute")` — find where it's defined AND who calls it.
2. `READ(match_id=<from definition>)` + `READ(match_id=<from callsite>)` — read definition and the most relevant caller in parallel.
Why: CallsiteSearch separates defs from calls. The callsite reveals how data flows INTO the vulnerable function — this is the path your PoC must follow.

### Input Format Analysis: CorpusInspect → HexView/StructProbe → BASH
1. `CorpusInspect("repo-vul")` — find seed files and their sizes/previews.
2. `HexView(path=<seed>, offset=0, length=64)` or `StructProbe(path=<seed>, offset=0, formats=...)` — inspect seed structure.
3. `BASH("python3 -c '...write mutated file...'")` — construct a candidate based on the observed format.
Why: Understanding the real input format from seeds is faster than guessing. StructProbe decodes fields; HexView shows raw bytes; BASH writes the mutated candidate.

### Binary Candidate Construction: HexView → BASH → submit_poc
1. `HexView(path=<seed>)` — identify magic bytes, header structure, and the offset to mutate.
2. `BASH("python3 -m toolbox <format> minimal > poc.bin && python3 -m toolbox mutate patch --file poc.bin --offset N --hex AA BB")` — generate a valid carrier and patch the target offset.
3. `submit_poc(poc_path="poc.bin")` — verify the candidate.
Why: Toolbox generates format-valid carriers; patching at a precise offset targets the vulnerability without breaking the parser.

### Miss Feedback Loop: submit_poc → READ → BASH → submit_poc
1. `submit_poc(...)` — get crash trace and vul_exit_code.
2. If `no_trigger`: `READ(match_id=<to vulnerable function>)` — re-read the exact condition that wasn't satisfied.
3. `BASH("python3 ...")` — construct a revised candidate addressing the gap.
4. `submit_poc(...)` — verify again.
Why: Submit feedback is the oracle. A no_trigger means the input never reached the bug — READ the function again to understand WHY, then fix the candidate.

### Parallel Chain Coverage (investigation phase)
Call these together in one step to cover the full data flow:
- `READ(match_id=<harness entry>)` — entrypoint
- `READ(match_id=<parser function>)` — parser
- `READ(match_id=<vulnerable function>)` — crash site
- `CallsiteSearch("<vuln_function>")` — call graph for the crash function
Why: Reading the full chain in parallel gives you the complete input→crash path in one step, enabling candidate construction immediately after.
