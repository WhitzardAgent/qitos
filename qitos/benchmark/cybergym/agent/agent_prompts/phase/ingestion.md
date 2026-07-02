
## Current Phase Guidance
- Read `README.md` first, then inspect the local task files and repository structure.
- Get oriented quickly with `RepoMap("repo-vul")` to find harness files, corpus dirs, and source layout.
- Use `GREP("LLVMFuzzerTestOneInput", path="repo-vul")` to confirm the harness entry. Follow the match_id to READ the harness body.
- Use `FindSymbols("LLVMFuzzerTestOneInput", kind="function")` to get the harness signature directly.
- Call `RepoMap` + `GREP` in parallel to orient in one step: RepoMap gives the map, GREP finds the entry.
- Move to `READ` on real source files instead of staying in listing mode.
- This is a quick orientation phase. Deep exploration of the call chain and constraints happens in the next phase.
