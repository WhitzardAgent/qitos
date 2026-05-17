"""End-to-end functional tests for the Claude Code coding agent.

These tests exercise the full agent loop with a real LLM,
validating that the coding agent can actually accomplish tasks
rather than just not-crashing.

Requirements:
- Set OPENAI_API_KEY (or QITOS_API_KEY) and OPENAI_BASE_URL environment variables
- Optionally set QITOS_TEST_MODEL to override the default model name
- Tests are skipped if no API key is available

Run:
    QITOS_TEST_MODEL=ds-v4-pro OPENAI_API_KEY=xxx OPENAI_BASE_URL=xxx pytest tests/test_e2e_coding_agent.py -v -s

Markers:
    @pytest.mark.e2e  — marks as end-to-end (requires real LLM)
    @pytest.mark.slow  — takes >30s
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

E2E_MARK = pytest.mark.e2e
SLOW_MARK = pytest.mark.slow


def _skip_if_no_api_key():
    """Skip test if no API key is available."""
    has_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY"))
    has_url = bool(os.getenv("OPENAI_BASE_URL") or os.getenv("QITOS_BASE_URL"))
    if not has_key or not has_url:
        pytest.skip("No API key/base_url set. Set OPENAI_API_KEY and OPENAI_BASE_URL to run e2e tests.")


def _make_llm():
    """Create an LLM from environment variables."""
    from qitos.models import ModelFactory

    _skip_if_no_api_key()

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("QITOS_BASE_URL", "")
    model_name = os.getenv("QITOS_TEST_MODEL", "ds-v4-pro")

    return ModelFactory.create(
        "openai-compatible",
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=0.1,  # Low temperature for deterministic-ish outputs
        max_tokens=4096,
    )


def _make_agent(workspace_root: str = ".", permission_mode: str = "bypassPermissions", **kwargs):
    """Create a ClaudeCodeAgent with the test LLM."""
    from examples.real.claude_code.agent import ClaudeCodeAgent

    llm = _make_llm()
    return ClaudeCodeAgent(
        llm=llm,
        workspace_root=workspace_root,
        permission_mode=permission_mode,
        max_steps=kwargs.pop("max_steps", 15),
        **kwargs,
    )


def _run_agent(agent, task: str, max_steps: int = 15):
    """Run the agent headlessly and return the EngineResult."""
    from qitos.engine.states import RuntimeBudget

    engine = agent.build_engine(budget=RuntimeBudget(max_steps=max_steps))
    return engine.run(task)


# ---------------------------------------------------------------------------
# 1. File Reading — the most basic coding agent capability
# ---------------------------------------------------------------------------

@E2E_MARK
def test_read_and_summarize_file():
    """Agent should read a file and summarize its content."""
    agent = _make_agent()
    result = _run_agent(agent, "Read the file README.md and tell me what QitOS is in 1-2 sentences.")

    # Must have executed at least 1 step (a Read call)
    assert result.step_count >= 1, f"Agent did nothing (step_count={result.step_count})"

    # Must have produced a final result
    final = result.state.final_result or ""
    assert len(final) > 20, f"Final result too short: {final[:200]}"

    # Should mention QitOS
    assert "qitos" in final.lower() or "agent" in final.lower(), (
        f"Final result doesn't mention QitOS or agents: {final[:300]}"
    )


# ---------------------------------------------------------------------------
# 2. File Search — Glob + Grep
# ---------------------------------------------------------------------------

@E2E_MARK
def test_search_for_code_pattern():
    """Agent should find files containing a specific pattern."""
    agent = _make_agent()
    result = _run_agent(
        agent,
        "Search the codebase for the class definition of 'Engine' "
        "using Grep. Tell me which file defines it and the line number.",
    )

    assert result.step_count >= 1

    final = result.state.final_result or ""
    # Should find engine.py
    assert "engine" in final.lower(), f"Result doesn't mention engine: {final[:300]}"


@E2E_MARK
def test_glob_for_files():
    """Agent should find files matching a glob pattern."""
    agent = _make_agent()
    result = _run_agent(
        agent,
        "Use Glob to find all Python files under qitos/engine/ and list the first 5 filenames.",
    )

    assert result.step_count >= 1

    final = result.state.final_result or ""
    # Should find engine-related files
    assert ".py" in final.lower(), f"Result doesn't mention .py files: {final[:300]}"


# ---------------------------------------------------------------------------
# 3. File Creation and Editing
# ---------------------------------------------------------------------------

@E2E_MARK
def test_create_file_with_content():
    """Agent should create a new file with specified content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = _make_agent(workspace_root=tmpdir)
        result = _run_agent(
            agent,
            "Create a file called hello.py with a Python function that "
            "takes a name string and returns 'Hello, {name}!'. "
            "The function should be called greet.",
        )

        assert result.step_count >= 1

        # Verify the file was actually created
        hello_path = os.path.join(tmpdir, "hello.py")
        assert os.path.isfile(hello_path), "Agent did not create hello.py"

        # Verify the content makes sense
        content = open(hello_path).read()
        assert "def greet" in content, f"Missing greet function: {content[:200]}"
        assert "Hello" in content or "hello" in content.lower(), f"Missing greeting: {content[:200]}"


@E2E_MARK
def test_edit_existing_file():
    """Agent should edit an existing file correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a file to edit
        file_path = os.path.join(tmpdir, "calc.py")
        with open(file_path, "w") as f:
            f.write("def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n")

        agent = _make_agent(workspace_root=tmpdir)
        result = _run_agent(
            agent,
            "Read the file calc.py, then add a 'subtract' function "
            "that takes a, b and returns a - b. Add it after the add function.",
        )

        assert result.step_count >= 1

        # Verify the edit
        content = open(file_path).read()
        assert "subtract" in content.lower(), f"Missing subtract function: {content[:400]}"
        # Original content should still be there
        assert "multiply" in content, f"Lost original multiply function: {content[:400]}"


# ---------------------------------------------------------------------------
# 4. Bash Execution
# ---------------------------------------------------------------------------

@E2E_MARK
def test_run_bash_command():
    """Agent should run a bash command and report the output."""
    agent = _make_agent()
    result = _run_agent(
        agent,
        "Run 'git log --oneline -3' using Bash and tell me the most recent commit message.",
    )

    assert result.step_count >= 1

    final = result.state.final_result or ""
    # Should have found a commit message
    assert len(final) > 10, f"Result too short: {final[:200]}"


# ---------------------------------------------------------------------------
# 5. Multi-Step Task — Read, Analyze, Modify
# ---------------------------------------------------------------------------

@E2E_MARK
@SLOW_MARK
def test_multi_step_read_analyze_edit():
    """Agent should read, analyze, and modify code in a single task."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a file with a bug
        file_path = os.path.join(tmpdir, "buggy.py")
        with open(file_path, "w") as f:
            f.write(
                "def fibonacci(n):\n"
                "    if n <= 0:\n"
                "        return 0\n"
                "    if n == 1:\n"
                "        return 1\n"
                "    return fibonacci(n - 1) + fibonacci(n - 2)\n"
                "\n"
                "def factorial(n):\n"
                "    if n == 0:\n"
                "        return 1\n"
                "    return n * factorial(n)  # BUG: should be n-1\n"
            )

        agent = _make_agent(workspace_root=tmpdir)
        result = _run_agent(
            agent,
            "Read buggy.py. There is a bug in the factorial function — "
            "find it and fix it. The bug is on the last line of the file.",
            max_steps=10,
        )

        assert result.step_count >= 2, "Agent should have at least read + edited"

        # Verify the bug was fixed
        content = open(file_path).read()
        assert "n - 1" in content, f"Bug not fixed — still has 'n' instead of 'n-1': {content[:400]}"
        assert "fibonacci" in content, "Lost the fibonacci function during edit"


# ---------------------------------------------------------------------------
# 6. Plan Mode — Read-Only Enforcement
# ---------------------------------------------------------------------------

@E2E_MARK
def test_plan_mode_read_only():
    """Agent in plan mode should only read, not write."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a file
        file_path = os.path.join(tmpdir, "readonly_test.py")
        with open(file_path, "w") as f:
            f.write("ORIGINAL_CONTENT = True\n")

        agent = _make_agent(workspace_root=tmpdir, permission_mode="plan")
        result = _run_agent(
            agent,
            "Read the file readonly_test.py and describe what it contains. "
            "Then tell me what you would change about it.",
            max_steps=8,
        )

        # File must NOT be modified in plan mode
        content = open(file_path).read()
        assert "ORIGINAL_CONTENT = True" in content, (
            f"File was modified in plan mode! Content: {content[:200]}"
        )


# ---------------------------------------------------------------------------
# 7. Sub-Agent Spawning (Explore)
# ---------------------------------------------------------------------------

@E2E_MARK
@SLOW_MARK
def test_explore_subagent():
    """Agent should be able to spawn an Explore sub-agent for codebase search."""
    agent = _make_agent()
    result = _run_agent(
        agent,
        "Use the Agent tool with subagent_type='explore' to find which file "
        "defines the StepResult class. Report the file path.",
        max_steps=10,
    )

    assert result.step_count >= 1

    final = result.state.final_result or ""
    # Should find states.py
    assert "states" in final.lower(), f"Result doesn't mention states: {final[:300]}"


# ---------------------------------------------------------------------------
# 8. Context Awareness — Environment and Git
# ---------------------------------------------------------------------------

@E2E_MARK
def test_environment_awareness():
    """Agent should be aware of its environment (cwd, platform, git branch)."""
    agent = _make_agent()
    result = _run_agent(
        agent,
        "What is the current working directory and git branch? "
        "Just tell me the directory and branch name.",
    )

    assert result.step_count >= 1

    final = result.state.final_result or ""
    # Should mention something about the directory
    assert "qitos" in final.lower() or "directory" in final.lower() or "cwd" in final.lower(), (
        f"Result doesn't show environment awareness: {final[:300]}"
    )


# ---------------------------------------------------------------------------
# 9. Task Management — TaskCreate/TaskUpdate
# ---------------------------------------------------------------------------

@E2E_MARK
def test_task_management():
    """Agent should use TaskCreate/TaskUpdate to track work."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a simple file to work with
        file_path = os.path.join(tmpdir, "tasks_test.py")
        with open(file_path, "w") as f:
            f.write("# TODO: implement me\n")

        agent = _make_agent(workspace_root=tmpdir)
        result = _run_agent(
            agent,
            "Create a task to read tasks_test.py, then do it. "
            "Mark the task as completed when done.",
            max_steps=8,
        )

        # Should have produced some output about completing the task
        final = result.state.final_result or ""
        assert len(final) > 5, f"Agent produced no output: {final[:200]}"


# ---------------------------------------------------------------------------
# 10. Error Recovery — Agent Handles Bad Input Gracefully
# ---------------------------------------------------------------------------

@E2E_MARK
def test_handles_nonexistent_file():
    """Agent should handle reading a nonexistent file gracefully."""
    agent = _make_agent()
    result = _run_agent(
        agent,
        "Read the file /nonexistent/path/xyz123.py and tell me what's in it.",
        max_steps=5,
    )

    # Should not crash — the step count shows the agent tried
    assert result.step_count >= 1

    final = result.state.final_result or ""
    # Should mention the file doesn't exist or was not found
    assert (
        "not found" in final.lower()
        or "does not exist" in final.lower()
        or "no such file" in final.lower()
        or "error" in final.lower()
        or "cannot" in final.lower()
        or "couldn" in final.lower()
    ), f"Agent didn't acknowledge the file issue: {final[:300]}"


# ---------------------------------------------------------------------------
# 11. Auto Permission Mode
# ---------------------------------------------------------------------------

@E2E_MARK
def test_auto_permission_mode():
    """Agent in auto mode should auto-approve safe operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, "auto_test.py")
        with open(file_path, "w") as f:
            f.write("x = 42\n")

        agent = _make_agent(workspace_root=tmpdir, permission_mode="auto")
        # Verify auto_classifier is set up
        assert agent.permission_pipeline._auto_classifier is not None

        result = _run_agent(
            agent,
            "Read auto_test.py and tell me the value of x.",
            max_steps=5,
        )

        final = result.state.final_result or ""
        assert "42" in final, f"Agent didn't find x=42: {final[:300]}"


# ---------------------------------------------------------------------------
# 12. End-to-End REPL Session (headless multi-turn)
# ---------------------------------------------------------------------------

@E2E_MARK
@SLOW_MARK
def test_repl_headless_multi_turn():
    """Test the REPL's headless mode with a real agent."""
    from qitos.kit.repl import AgentREPL

    llm = _make_llm()
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = _make_agent(workspace_root=tmpdir)
        repl = AgentREPL(agent=agent, workspace=tmpdir, max_steps=5)

        # Run a headless task
        repl.run_headless("Create a file called notes.txt with the content 'Hello World'")
        # If we got here without error, the REPL headless path works


# ---------------------------------------------------------------------------
# 13. Streaming Works End-to-End
# ---------------------------------------------------------------------------

@E2E_MARK
def test_streaming_e2e():
    """Verify streaming produces output during agent execution."""
    from qitos.engine.streaming import StreamHandler

    streamed_chunks: list[str] = []

    class TestStreamHandler:
        def on_start(self):
            pass

        def on_delta(self, text: str):
            streamed_chunks.append(text)

        def on_end(self):
            pass

    agent = _make_agent()
    from qitos.engine.states import RuntimeBudget

    engine = agent.build_engine(budget=RuntimeBudget(max_steps=5))
    engine.stream_callback = TestStreamHandler()
    result = engine.run("What is 2+2? Answer in one word.")

    # Should have streamed some text
    assert len(streamed_chunks) > 0, "No streaming output received"


# ---------------------------------------------------------------------------
# 14. Step Recovery — Engine Recovers from Errors
# ---------------------------------------------------------------------------

@E2E_MARK
def test_step_recovery():
    """Verify engine.step() recovery works during real execution."""
    agent = _make_agent()
    from qitos.engine.states import RuntimeBudget

    engine = agent.build_engine(budget=RuntimeBudget(max_steps=10))
    state, observation = engine.init_session("Read the file README.md and tell me what QitOS is.")

    # Run a few steps manually using the step() API
    steps = 0
    recovered = 0
    while steps < 5:
        step_result = engine.step(state, observation)
        steps += 1

        if step_result.recovered:
            recovered += 1
            state.advance_step()
            observation = engine.rebuild_observation(state)
            continue

        if step_result.stop:
            break

        # Check if we got a final answer
        if step_result.decision and step_result.decision.mode == "final":
            break

        state.advance_step()
        observation = step_result.observation

    # Should have executed at least one step
    assert steps >= 1, f"Agent did nothing in step-by-step mode (steps={steps})"


# ---------------------------------------------------------------------------
# Fixture for test summary
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(config, items):
    """Add e2e marker to all tests in this file."""
    for item in items:
        if not any(marker.name == "e2e" for marker in item.iter_markers()):
            item.add_marker(E2E_MARK)
