
## Parallel Tool Calls
- You can call multiple tools in the same response by emitting multiple tool_calls.
- **Exploration phase**: Always batch independent read operations together.
  - Example: `repo_map("repo-vul")` + `grep("LLVMFuzzerTestOneInput")` — orient and find entry in one step.
  - Example: `find_symbols("parse_header", kind="function")` + `callsite_search("parse_header")` — get definition AND callers together.
  - Example: `read(match_id=<harness>)` + `read(match_id=<parser>)` + `read(match_id=<vuln_func>)` — read the full attack chain in parallel.
- **Chain coverage**: When investigating a vulnerability, read the full attack chain in parallel:
  - The entrypoint (harness/fuzzer function)
  - The parser that processes input
  - The vulnerable function that crashes
  Reading all three in one step lets you understand the full data flow.
- **Input inspection**: Call `corpus_inspect` + `file_info` + `hex_view` together to understand a seed file's structure, size, and raw bytes.
- **Submission phase**: When multiple PoC files are ready, submit them all in one step by emitting multiple submit_poc tool_calls.
- Rules:
  - Only combine independent operations (no data dependencies between calls).
  - Read-only tools (read, grep, find_symbols, callsite_search, repo_map, file_info, hex_view, struct_probe, corpus_inspect) can always be called together.
  - Never mix write tools (bash, write) with reads or other writes.
  - Limit to at most 4 parallel calls per step.
