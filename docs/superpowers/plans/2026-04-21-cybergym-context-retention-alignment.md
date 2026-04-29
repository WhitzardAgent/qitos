# CyberGym Context Retention Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep stable code and feedback facts visible across turns so the CyberGym single-agent runtime stops rereading files after old tool results are snipped.

**Architecture:** Reuse the existing single-agent CyberGym state and evidence index. Add a small durable working-memory layer, surface it in the system prompt and observation packet, and record it in step traces. Do not modify the generic compaction engine in the first slice.

**Tech Stack:** Python, QitOS agent runtime, pytest

---

### Task 1: Add Durable Working-Memory State

**Files:**
- Modify: `qitos/benchmark/cybergym/agent/state.py`
- Test: `tests/test_cybergym_agent_poc_profile.py`

- [ ] **Step 1: Add durable-memory fields to `CyberGymState`**

Add the following fields near the existing runtime/evidence fields in `qitos/benchmark/cybergym/agent/state.py`:

```python
    durable_project_memory: Dict[str, Any] = field(default_factory=dict)
    durable_code_facts: List[str] = field(default_factory=list)
    durable_feedback_facts: List[str] = field(default_factory=list)
```

- [ ] **Step 2: Keep the new fields compatible with existing state construction**

Do not add custom serialization logic yet. The default dataclass behavior is sufficient because the new fields are plain dict/list containers.

- [ ] **Step 3: Add a state-level smoke test**

In `tests/test_cybergym_agent_poc_profile.py`, add a focused test like:

```python
def test_cybergym_state_initializes_durable_memory_fields():
    from qitos.benchmark.cybergym.agent.state import CyberGymState

    state = CyberGymState(task="demo")

    assert state.durable_project_memory == {}
    assert state.durable_code_facts == []
    assert state.durable_feedback_facts == []
```

- [ ] **Step 4: Run the new state test**

Run:

```bash
pytest tests/test_cybergym_agent_poc_profile.py::test_cybergym_state_initializes_durable_memory_fields -q
```

Expected: `1 passed`

### Task 2: Populate Durable Project Memory From Existing Evidence

**Files:**
- Modify: `qitos/benchmark/cybergym/agent/agent.py`
- Test: `tests/test_agent_multi_agent_runtime.py`

- [ ] **Step 1: Add a helper to normalize durable project memory**

In `qitos/benchmark/cybergym/agent/agent.py`, add a helper on `CyberGymAgent` with behavior equivalent to:

```python
    def _refresh_durable_project_memory(self, state: CyberGymState) -> None:
        evidence = dict(state.evidence_index or {})
        state.durable_project_memory = {
            "repo_summary": self._repo_prompt_summary(state.repo_index or ""),
            "parser_paths": list(evidence.get("parser_paths") or [])[:8],
            "seed_paths": list(evidence.get("seed_paths") or [])[:8],
            "field_paths": list(evidence.get("field_paths") or [])[:8],
        }
```

- [ ] **Step 2: Refresh durable project memory during family bootstrap**

In `_ensure_family_bootstrap`, after `state.evidence_index` is refreshed or validated, call:

```python
self._refresh_durable_project_memory(state)
```

This must happen even when the family pool already exists so the memory block stays synchronized with the current evidence index.

- [ ] **Step 3: Add a regression test for project-memory refresh**

In `tests/test_agent_multi_agent_runtime.py`, add a test like:

```python
def test_family_bootstrap_populates_durable_project_memory(tmp_path, make_agent):
    agent = make_agent(tmp_path)
    state = agent.init_state(
        "demo task",
        description="parser issue",
        source_root=str(tmp_path / "repo-vul"),
    )

    assert "parser_paths" in state.durable_project_memory
    assert "seed_paths" in state.durable_project_memory
    assert "field_paths" in state.durable_project_memory
```
```

Adjust setup to match existing test fixtures in that file.

- [ ] **Step 4: Run the bootstrap-memory test**

Run:

```bash
pytest tests/test_agent_multi_agent_runtime.py -k durable_project_memory -q
```

Expected: matching test passes

### Task 3: Add Durable Code / Feedback Facts

**Files:**
- Modify: `qitos/benchmark/cybergym/agent/agent.py`
- Test: `tests/test_agent_submit_runtime.py`

- [ ] **Step 1: Add capped deduplicating fact helpers**

In `qitos/benchmark/cybergym/agent/agent.py`, add helpers equivalent to:

```python
    @staticmethod
    def _append_capped_fact(items: List[str], fact: str, *, limit: int = 8) -> List[str]:
        text = str(fact or "").strip()
        if not text:
            return items
        filtered = [entry for entry in items if entry != text]
        filtered.append(text)
        return filtered[-limit:]
```

and:

```python
    def _capture_read_fact(self, state: CyberGymState, short_name: str, output: Any) -> None:
        ...

    def _capture_feedback_fact(self, state: CyberGymState, output: Dict[str, Any]) -> None:
        ...
```

- [ ] **Step 2: Capture code facts from `READ` results**

Use `_capture_read_fact` inside `_process_action_result` after `observation_note` is produced.

Keep only short stable facts such as:

- `entrypoint: <path>`
- `constraint: <path> -> <clipped snippet>`

Do not store entire file contents.

- [ ] **Step 3: Capture feedback facts from `submit_poc` results**

Inside the existing `submit_poc` branch in `_process_action_result`, after parsing verification/crash hints, call `_capture_feedback_fact`.

Preserve short facts such as:

- parser reject phrase
- crash type
- crash location
- clipped verification hint

- [ ] **Step 4: Add a submit-runtime test**

In `tests/test_agent_submit_runtime.py`, add a test that feeds a synthetic `submit_poc` result into `_process_action_result` and asserts at least one durable feedback fact is stored.

Example assertion shape:

```python
assert state.durable_feedback_facts
assert any("Invalid record" in fact or "heap-buffer-overflow" in fact for fact in state.durable_feedback_facts)
```

- [ ] **Step 5: Run the submit-runtime test**

Run:

```bash
pytest tests/test_agent_submit_runtime.py -k durable_feedback -q
```

Expected: matching test passes

### Task 4: Surface Durable Working Memory In Prompt And Observation

**Files:**
- Modify: `qitos/benchmark/cybergym/agent/agent.py`
- Test: `tests/test_agent_prompting.py`

- [ ] **Step 1: Add system-prompt guidance mirroring Claude Code**

Extend `base_persona_prompt()` with a short section conveying:

```text
- Older tool results may be cleared from context later.
- If a read reveals information that will matter later, capture the important fact in working memory instead of assuming the original output will remain visible.
- Before rereading, check the working-memory block first.
```

- [ ] **Step 2: Add working-memory render helpers**

Add helpers like:

```python
    def _working_memory_lines(self, state: CyberGymState) -> List[str]:
        ...

    def _project_memory_lines(self, state: CyberGymState) -> List[str]:
        ...
```

These should render Markdown bullets for:

- project index
- durable code facts
- durable feedback facts

- [ ] **Step 3: Include working memory in the observation packet**

In `_build_initial_brief` and `_build_observation_packet` paths, append a dedicated Markdown section:

```text
## Working Memory
### Project Index
...
### Durable Code Facts
...
### Durable Feedback Facts
...
```

Keep it concise and deterministic.

- [ ] **Step 4: Add a prompt test**

In `tests/test_agent_prompting.py`, add a test asserting:

- the system prompt contains the tool-result-clearing guidance
- the observation contains `## Working Memory` when durable facts exist

- [ ] **Step 5: Run the prompt test**

Run:

```bash
pytest tests/test_agent_prompting.py -k working_memory -q
```

Expected: matching test passes

### Task 5: Add Working Memory To Step Trace Context

**Files:**
- Modify: `qitos/benchmark/cybergym/agent/agent.py`
- Test: `tests/test_agent_prompting.py`

- [ ] **Step 1: Extend `_step_context_payload`**

Add fields like:

```python
        payload["durable_project_memory"] = state.durable_project_memory
        payload["durable_code_facts"] = list(state.durable_code_facts or [])
        payload["durable_feedback_facts"] = list(state.durable_feedback_facts or [])
```

- [ ] **Step 2: Keep the payload JSON-safe and compact**

Do not dump large repo indexes. Use only the normalized `durable_project_memory` summary from Task 2.

- [ ] **Step 3: Add a trace-context test**

In `tests/test_agent_prompting.py`, add a focused test that builds a state with durable facts, calls `_step_context_payload`, and asserts the new keys are present.

- [ ] **Step 4: Run the trace-context test**

Run:

```bash
pytest tests/test_agent_prompting.py -k step_context_payload -q
```

Expected: matching test passes

### Task 6: Run Focused Verification

**Files:**
- Modify: none
- Test: existing targeted test files

- [ ] **Step 1: Run the focused CyberGym agent test set**

Run:

```bash
pytest \
  tests/test_cybergym_agent_poc_profile.py \
  tests/test_agent_multi_agent_runtime.py \
  tests/test_agent_submit_runtime.py \
  tests/test_agent_prompting.py \
  -q
```

Expected: all selected tests pass

- [ ] **Step 2: Record any failures and fix only retention-alignment regressions**

If any failures occur, make the smallest fix necessary in `state.py` or `agent.py`, then rerun the same command.

- [ ] **Step 3: Smoke-check the runtime import path**

Run:

```bash
python - <<'PY'
from qitos.benchmark.cybergym.agent.agent import CyberGymAgent
print(CyberGymAgent.name)
PY
```

Expected:

```text
cybergym_poc_gen
```
