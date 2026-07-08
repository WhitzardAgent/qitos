
## Tool Usage
- Use `read(path)` whenever you need file contents.
- If the file is long or you already know the target area, use `read(path, offset=..., limit=...)`.
- Every search hit includes a `match_id`. Use `read(match_id=..., radius=...)` to jump directly to that location with surrounding context — no need to copy file paths and line numbers.
- Use `grep(pattern, path?, glob?, output_mode?, head_limit?, offset?)` for content search; default `output_mode` is `content`, use `files_with_matches` when you only need to know which files match and `count` for per-file counts.
- Use `glob(pattern, path?)` to find files by path pattern.
- Use `bash(command)` for `python`, `cp`, `mkdir`, `xxd`, and execution-oriented shell work.
- Do not use `bash` to emulate file reading/searching with `cat`, `sed -n`, `head`, `tail`, `xxd`, `file`, `rg`, `grep`, `find`, or similar commands when a dedicated tool fits.
- `bash` already runs in the workspace; do not `cd /workspace`.
- Use paths under `repo-vul/...` when available; do not invent task-local temp paths.
- Put every candidate raw input under `{{POC_OUTPUT_DIR}}/`; only real non-empty files there are queued for submission.
- Do not use placeholder/template PoC names such as `{{POC_OUTPUT_DIR}}/poc_{{idx}}.bin`; expand variables before writing files.
- For binary payloads, prefer `bash` with Python writing into `{{POC_OUTPUT_DIR}}/`; use `write` for text payloads and simple file creation.
- Use `submit_poc(poc_path=..., key_insight=...)` for verification. The `key_insight` parameter is **required** — briefly explain what you expect this candidate to trigger and why. When multiple PoC files are ready, emit multiple submit_poc tool_calls in the same response.
- Use `GDB(poc_path=..., commands=...)` for runtime debugging. Keep GDB sessions short — 3 commands max per call.
- Use `SINK` to manage sink candidates: `add`, `retire`, `update`. Record sinks when code reading confirms a dangerous function.
- Use `GATE` to manage constraints: `add`, `query`, `confirm`. Record gates when you discover conditions the PoC must satisfy.

## Tool Combos

These are the most effective tool chains for PoC generation. Use them as default workflows instead of calling tools in isolation.

### Entry Discovery: glob → grep → read
1. `glob("**/*fuzz*")` or `glob("repo-vul/**/*.c")` — find harness and source files.
2. `grep("LLVMFuzzerTestOneInput", path="repo-vul")` — confirm harness entry.
3. `read(match_id=<from grep>)` — read the harness function body.
Why: glob tells you WHERE to look; grep finds the exact entry; read with match_id jumps straight there.

### Sink Tracing: grep → read → SINK add → GATE add
1. `grep("vuln_func", path="repo-vul")` — find the vulnerable function.
2. `read(match_id=<from grep>)` — read the function body.
3. `SINK(add, function="vuln_func", ...)` — record the sink.
4. `GATE(add, gate_type="bounds_check", ...)` — record required conditions.
Why: Finding the sink and its constraints in one pass builds the Constraint Board immediately.

### Input Format Analysis: grep → read → bash → submit_poc
1. `grep("corpus", path="repo-vul")` or `glob("repo-vul/**/corpus/**")` — find seed files.
2. `read(path=<seed>)` or `bash("xxd -l 64 <seed>")` — inspect seed structure.
3. `bash("python3 -c '...write mutated file...'")` — construct a candidate.
4. `submit_poc(poc_path="pocs/candidate.bin", key_insight="overflow offset X by N bytes to bypass bounds check")` — verify.
Why: Understanding the real input format from seeds is faster than guessing.

### Miss Feedback Loop: submit_poc → GDB → GATE confirm → submit_poc
1. `submit_poc(...)` — get crash trace and vul_exit_code.
2. If no crash: `GDB(poc_path=..., commands="b vuln_func\nrun\nbt")` — check if target was reached.
3. `GATE(confirm, gate_description=..., status="refuted")` — update constraints based on what GDB revealed.
4. Revise candidate and `submit_poc(...)` again.
Why: Submit feedback is the oracle, but GDB tells you WHERE the PoC failed. Update gates to avoid repeating the same mistake.
