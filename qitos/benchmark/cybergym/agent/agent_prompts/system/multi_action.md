
## Parallel Tool Calls
- You can call multiple tools in the same response by emitting multiple tool_calls.
- **Exploration phase**: Always batch independent read operations together.
  - Example: `RepoMap("repo-vul")` + `GREP("LLVMFuzzerTestOneInput")` — orient and find entry in one step.
  - Example: `FindSymbols("parse_header", kind="function")` + `CallsiteSearch("parse_header")` — get definition AND callers together.
  - Example: `READ(match_id=<harness>)` + `READ(match_id=<parser>)` + `READ(match_id=<vuln_func>)` — read the full attack chain in parallel.
- **Chain coverage**: When investigating a vulnerability, read the full attack chain in parallel:
  - The entrypoint (harness/fuzzer function)
  - The parser that processes input
  - The vulnerable function that crashes
  Reading all three in one step lets you understand the full data flow.
- **Input inspection**: Call `CorpusInspect` + `FileInfo` + `HexView` together to understand a seed file's structure, size, and raw bytes.
- **Submission phase**: When multiple PoC files are ready, submit them all in one step by emitting multiple submit_poc tool_calls.
- Rules:
  - Only combine independent operations (no data dependencies between calls).
  - Read-only tools (READ, GREP, FindSymbols, CallsiteSearch, RepoMap, FileInfo, HexView, StructProbe, CorpusInspect) can always be called together.
  - Never mix write tools (BASH, WRITE) with reads or other writes.
  - Limit to at most 4 parallel calls per step.
