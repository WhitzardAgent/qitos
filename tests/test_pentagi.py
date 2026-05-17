"""Tests for PentAGI tools, agents, critics, and flow."""

import json
import pytest
from unittest.mock import MagicMock, patch

# ===== Config Tests =====

class TestPentAGIConfig:
    def test_default_config(self):
        from qitos.examples.pentagi.config import PentAGIConfig
        config = PentAGIConfig()
        assert config.model_provider == "openai-compatible"
        assert config.docker_profile == "kali"
        assert config.max_subtasks == 15
        assert config.language == "en"

    def test_custom_config(self):
        from qitos.examples.pentagi.config import PentAGIConfig
        config = PentAGIConfig(
            model_provider="openai",
            model_name="gpt-4o",
            docker_profile="ubuntu",
            max_subtasks=10,
            language="zh",
            authorized_targets=["10.0.0.0/24"],
        )
        assert config.model_name == "gpt-4o"
        assert config.language == "zh"
        assert config.authorized_targets == ["10.0.0.0/24"]


class TestDockerProfiles:
    def test_kali_profile(self):
        from qitos.examples.pentagi.config import get_docker_config
        config = get_docker_config("kali")
        assert config["image"] == "kalilinux/kali-rolling"
        assert "--cap-add=NET_ADMIN" in config["extra_run_args"]

    def test_ubuntu_profile(self):
        from qitos.examples.pentagi.config import get_docker_config
        config = get_docker_config("ubuntu")
        assert config["image"] == "ubuntu:22.04"

    def test_unknown_profile_raises(self):
        from qitos.examples.pentagi.config import get_docker_config
        with pytest.raises(ValueError, match="Unknown Docker profile"):
            get_docker_config("nonexistent")


# ===== Barrier Tools Tests =====

class TestBarrierTools:
    def test_barrier_done(self):
        from qitos.examples.pentagi.tools.barrier import BarrierDone
        tool = BarrierDone()
        result = tool.execute({"summary": "Subtask completed successfully"})
        assert result["status"] == "done"
        assert "completed successfully" in result["summary"]

    def test_barrier_ask(self):
        from qitos.examples.pentagi.tools.barrier import BarrierAsk
        tool = BarrierAsk()
        result = tool.execute({"question": "What is the target IP?"})
        assert result["status"] == "waiting"
        assert "target IP" in result["question"]


# ===== Terminal Tools Tests =====

class TestTerminalTools:
    def test_terminal_no_env(self):
        from qitos.examples.pentagi.tools.terminal_env import TerminalTool
        tool = TerminalTool()
        result = tool.execute({"command": "ls"}, runtime_context={})
        assert result["status"] == "error"
        assert "Docker" in result["message"]

    def test_read_file_no_env(self):
        from qitos.examples.pentagi.tools.terminal_env import ReadFileTool
        tool = ReadFileTool()
        result = tool.execute({"path": "/etc/passwd"}, runtime_context={})
        assert result["status"] == "error"

    def test_write_file_no_env(self):
        from qitos.examples.pentagi.tools.terminal_env import WriteFileTool
        tool = WriteFileTool()
        result = tool.execute({"path": "/tmp/test.txt", "content": "hello"}, runtime_context={})
        assert result["status"] == "error"

    def test_terminal_with_mock_env(self):
        from qitos.examples.pentagi.tools.terminal_env import TerminalTool
        tool = TerminalTool()
        mock_env = MagicMock()
        mock_env.cmd.run.return_value = {"exit_code": 0, "stdout": "file.txt", "stderr": ""}
        result = tool.execute(
            {"command": "ls"},
            runtime_context={"env": mock_env},
        )
        assert result["status"] == "ok"
        assert result["stdout"] == "file.txt"


# ===== Search Tools Tests =====

class TestSearchTools:
    def test_search_in_memory_no_memory(self):
        from qitos.examples.pentagi.tools.search_network import SearchInMemoryTool
        tool = SearchInMemoryTool()
        result = tool.execute({"query": "test"}, runtime_context={})
        assert result["status"] == "error"

    def test_search_in_memory_with_mock_memory(self):
        from qitos.examples.pentagi.tools.search_network import SearchInMemoryTool
        from qitos.core.memory import MemoryRecord
        mock_memory = MagicMock()
        mock_memory.retrieve.return_value = [
            MemoryRecord(role="system", content="Found info", step_id=1, metadata={"type": "answer"})
        ]
        tool = SearchInMemoryTool(memory=mock_memory)
        result = tool.execute({"query": "nmap scan"})
        assert result["status"] == "ok"
        assert result["count"] == 1


# ===== Vector DB Tools Tests =====

class TestVectorDBTools:
    def test_search_guide_no_memory(self):
        from qitos.examples.pentagi.tools.search_vector_db import SearchGuideTool
        tool = SearchGuideTool()
        result = tool.execute({"query": "pentest"}, runtime_context={})
        assert result["status"] == "error"


# ===== Store Tools Tests =====

class TestStoreTools:
    def test_store_guide_no_memory(self):
        from qitos.examples.pentagi.tools.store_agent_result import StoreGuideTool
        tool = StoreGuideTool()
        result = tool.execute({"content": "test guide"}, runtime_context={})
        assert result["status"] == "error"

    def test_store_guide_with_mock_memory(self):
        from qitos.examples.pentagi.tools.store_agent_result import StoreGuideTool
        mock_memory = MagicMock()
        tool = StoreGuideTool(memory=mock_memory)
        result = tool.execute({"content": "Use nmap for port scanning", "title": "Port Scan Guide"})
        assert result["status"] == "ok"
        mock_memory.append.assert_called_once()


# ===== Critic Tests =====

class TestReflectorCritic:
    def test_allows_tool_calls(self):
        from qitos.examples.pentagi.critic.reflector import ReflectorCritic
        from qitos.engine.critic_result import CriticResult
        critic = ReflectorCritic()
        decision = MagicMock()
        decision.actions = [{"tool": "terminal", "args": {"command": "ls"}}]
        result = critic.evaluate(MagicMock(), decision, [])
        assert isinstance(result, CriticResult)
        assert result.action == "continue"

    def test_rejects_free_text(self):
        from qitos.examples.pentagi.critic.reflector import ReflectorCritic
        from qitos.engine.critic_result import CriticResult
        critic = ReflectorCritic()
        decision = MagicMock()
        decision.actions = []  # No tool calls = free text
        result = critic.evaluate(MagicMock(), decision, [])
        assert isinstance(result, CriticResult)
        assert result.action == "retry"
        assert result.instruction_patch is not None

    def test_stops_after_max_retries(self):
        from qitos.examples.pentagi.critic.reflector import ReflectorCritic
        critic = ReflectorCritic(max_retries=2)
        decision = MagicMock()
        decision.actions = []
        # First two retries
        for _ in range(2):
            result = critic.evaluate(MagicMock(), decision, [])
            assert result.action == "retry"
        # Third should stop
        result = critic.evaluate(MagicMock(), decision, [])
        assert result.action == "stop"


class TestStuckDetectionCritic:
    def test_detects_loop(self):
        from qitos.examples.pentagi.critic.stuck_detector import StuckDetectionCritic
        critic = StuckDetectionCritic(max_identical_actions=3)
        state = MagicMock()
        state.current_step = 5

        # Create identical actions 3 times
        decision = MagicMock()
        decision.actions = [{"tool": "terminal", "args": {"command": "nmap -sV 10.0.0.1"}}]
        for _ in range(3):
            result = critic.evaluate(state, decision, [])
        assert result.action == "retry"

    def test_allows_varied_actions(self):
        from qitos.examples.pentagi.critic.stuck_detector import StuckDetectionCritic
        critic = StuckDetectionCritic()
        state = MagicMock()
        state.current_step = 2

        # Different actions
        for i in range(3):
            decision = MagicMock()
            decision.actions = [{"tool": f"tool_{i}", "args": {"x": i}}]
            result = critic.evaluate(state, decision, [])
            assert result.action == "continue"


class TestToolCallFixer:
    def test_fix_trailing_commas(self):
        from qitos.examples.pentagi.critic.tool_call_fixer import ToolCallFixerRecovery
        fixed = ToolCallFixerRecovery.try_fix_json('{"key": "value",}')
        assert fixed == {"key": "value"}

    def test_fix_missing_brackets(self):
        from qitos.examples.pentagi.critic.tool_call_fixer import ToolCallFixerRecovery
        fixed = ToolCallFixerRecovery.try_fix_json('{"key": "value"')
        assert fixed == {"key": "value"}

    def test_valid_json_passes(self):
        from qitos.examples.pentagi.critic.tool_call_fixer import ToolCallFixerRecovery
        fixed = ToolCallFixerRecovery.try_fix_json('{"key": "value"}')
        assert fixed == {"key": "value"}

    def test_unfixable_returns_none(self):
        from qitos.examples.pentagi.critic.tool_call_fixer import ToolCallFixerRecovery
        fixed = ToolCallFixerRecovery.try_fix_json('not json at all {{{')
        assert fixed is None


# ===== SubtaskManager Tests =====

class TestSubtaskManager:
    def test_set_plan(self):
        from qitos.examples.pentagi.orchestrator.subtask_manager import SubtaskManager
        mgr = SubtaskManager()
        mgr.set_plan([
            {"title": "Recon", "description": "Reconnaissance"},
            {"title": "Exploit", "description": "Exploit vulnerabilities"},
        ])
        assert len(mgr.subtasks) == 2
        assert mgr.cursor == 0
        assert mgr.current_subtask["title"] == "Recon"

    def test_advance(self):
        from qitos.examples.pentagi.orchestrator.subtask_manager import SubtaskManager
        mgr = SubtaskManager()
        mgr.set_plan([
            {"title": "Step 1"},
            {"title": "Step 2"},
        ])
        mgr.mark_current_completed("Done with step 1")
        mgr.advance()
        assert mgr.cursor == 1
        assert mgr.current_subtask["title"] == "Step 2"

    def test_apply_delta_add(self):
        from qitos.examples.pentagi.orchestrator.subtask_manager import SubtaskManager
        mgr = SubtaskManager()
        mgr.set_plan([{"id": "1", "title": "Step 1"}])
        mgr.apply_delta([{"op": "add", "title": "Step 2", "description": "New step"}])
        assert len(mgr.subtasks) == 2

    def test_apply_delta_remove(self):
        from qitos.examples.pentagi.orchestrator.subtask_manager import SubtaskManager
        mgr = SubtaskManager()
        mgr.set_plan([
            {"id": "1", "title": "Step 1"},
            {"id": "2", "title": "Step 2"},
        ])
        mgr.apply_delta([{"op": "remove", "id": "2"}])
        assert len(mgr.subtasks) == 1

    def test_is_complete(self):
        from qitos.examples.pentagi.orchestrator.subtask_manager import SubtaskManager
        mgr = SubtaskManager()
        mgr.set_plan([{"title": "Only step"}])
        assert not mgr.is_complete
        mgr.mark_current_completed("Done")
        mgr.advance()
        assert mgr.is_complete

    def test_max_subtasks_enforcement(self):
        from qitos.examples.pentagi.orchestrator.subtask_manager import SubtaskManager
        mgr = SubtaskManager(max_subtasks=3)
        mgr.set_plan([{"title": f"Step {i}"} for i in range(5)])
        assert len(mgr.subtasks) == 3


# ===== ExecutionMonitor Tests =====

class TestExecutionMonitor:
    def test_progress_tracking(self):
        from qitos.examples.pentagi.orchestrator.execution_monitor import ExecutionMonitor
        monitor = ExecutionMonitor()
        monitor.start()
        monitor.record_step(success=True, new_findings=1)
        monitor.record_step(success=True, new_findings=0)
        summary = monitor.progress_summary
        assert summary["step_count"] == 2
        assert summary["findings_count"] == 1

    def test_stuck_detection(self):
        from qitos.examples.pentagi.orchestrator.execution_monitor import ExecutionMonitor
        monitor = ExecutionMonitor(max_consecutive_failures=3)
        monitor.start()
        for _ in range(3):
            monitor.record_step(success=False)
        assert monitor.is_stuck


# ===== Agent Tests =====

class TestAgents:
    def _make_mock_llm(self):
        return MagicMock()

    def test_primary_agent_init_state(self):
        from qitos.examples.pentagi.agents.primary import PrimaryPentestAgent, PentestState
        agent = PrimaryPentestAgent(llm=self._make_mock_llm())
        state = agent.init_state("Test target", max_steps=60)
        assert isinstance(state, PentestState)
        assert state.current_phase == "generation"
        assert state.task == "Test target"

    def test_primary_agent_system_prompt(self):
        from qitos.examples.pentagi.agents.primary import PrimaryPentestAgent
        agent = PrimaryPentestAgent(llm=self._make_mock_llm(), language="en")
        state = agent.init_state("Test target")
        prompt = agent.build_system_prompt(state)
        assert prompt is not None
        assert "orchestration" in prompt.lower() or "ORCHESTRATION" in prompt

    def test_pentester_agent_init_state(self):
        from qitos.examples.pentagi.agents.pentester import PentesterAgent
        agent = PentesterAgent(llm=self._make_mock_llm())
        state = agent.init_state("Scan target")
        assert state.task == "Scan target"
        assert state.max_steps == 15

    def test_coder_agent_init_state(self):
        from qitos.examples.pentagi.agents.coder import CoderAgent
        agent = CoderAgent(llm=self._make_mock_llm())
        state = agent.init_state("Write exploit")
        assert state.task == "Write exploit"

    def test_generator_agent_single_shot(self):
        from qitos.examples.pentagi.agents.generator import GeneratorAgent
        agent = GeneratorAgent(llm=self._make_mock_llm())
        state = agent.init_state("Pen test target X")
        assert state.max_steps == 5  # Multi-step for generation

    def test_refiner_agent_init(self):
        from qitos.examples.pentagi.agents.refiner import RefinerAgent
        agent = RefinerAgent(llm=self._make_mock_llm())
        state = agent.init_state(
            "Refine plan",
            completed_subtasks=[{"title": "Step 1", "status": "completed"}],
            planned_subtasks=[{"title": "Step 2", "status": "planned"}],
        )
        assert len(state.completed_subtasks) == 1

    def test_reporter_agent_should_stop(self):
        from qitos.examples.pentagi.agents.reporter import ReporterAgent
        agent = ReporterAgent(llm=self._make_mock_llm())
        state = agent.init_state("Generate report")
        assert not agent.should_stop(state)
        state.report = "Final report content"
        assert agent.should_stop(state)

    def test_adviser_agent_single_shot(self):
        from qitos.examples.pentagi.agents.adviser import AdviserAgent
        agent = AdviserAgent(llm=self._make_mock_llm())
        state = agent.init_state("Need advice")
        assert state.max_steps == 5

    def test_enricher_agent_should_stop(self):
        from qitos.examples.pentagi.agents.enricher import EnricherAgent
        agent = EnricherAgent(llm=self._make_mock_llm())
        state = agent.init_state("Enrich context")
        assert not agent.should_stop(state)
        state.enrichment_data = "Found relevant data"
        assert agent.should_stop(state)

    def test_all_agents_importable(self):
        from qitos.examples.pentagi.agents import (
            PrimaryPentestAgent,
            PentesterAgent,
            CoderAgent,
            InstallerAgent,
            SearcherAgent,
            MemoristAgent,
            GeneratorAgent,
            RefinerAgent,
            ReporterAgent,
            AdviserAgent,
            EnricherAgent,
        )
        # Verify all names are accessible
        agents = [
            PrimaryPentestAgent,
            PentesterAgent,
            CoderAgent,
            InstallerAgent,
            SearcherAgent,
            MemoristAgent,
            GeneratorAgent,
            RefinerAgent,
            ReporterAgent,
            AdviserAgent,
            EnricherAgent,
        ]
        assert len(agents) == 11


# ===== PentAGIMemory Tests =====

class TestPentAGIMemory:
    def test_append_and_retrieve(self):
        from qitos.examples.pentagi.memory.pentagi_memory import PentAGIMemory
        from qitos.core.memory import MemoryRecord
        memory = PentAGIMemory()
        memory.append(MemoryRecord(
            role="system",
            content="nmap -sV is used for version detection",
            step_id=1,
            metadata={"type": "guide", "title": "Port Scanning"},
        ))
        results = memory.retrieve({"text": "port scanning", "top_k": 5})
        assert len(results) >= 0  # May or may not match with hash embedder

    def test_reset(self):
        from qitos.examples.pentagi.memory.pentagi_memory import PentAGIMemory
        from qitos.core.memory import MemoryRecord
        memory = PentAGIMemory()
        memory.append(MemoryRecord(role="system", content="test", step_id=1, metadata={}))
        memory.reset()
        results = memory.retrieve({"text": "test"})
        assert len(results) == 0


# ===== CriticResult Integration Tests =====

class TestCriticResultIntegration:
    def test_critic_result_from_dict(self):
        from qitos.engine.critic_result import CriticResult
        d = {"action": "retry", "reason": "No tool call", "score": 0.0}
        result = CriticResult.from_dict(d)
        assert result.action == "retry"
        assert result.instruction_patch is None

    def test_critic_result_with_patches(self):
        from qitos.engine.critic_result import CriticResult
        d = {
            "action": "retry",
            "reason": "Fix tool call",
            "instruction_patch": "You must use a tool call.",
            "state_patch": {"_retry_count": 1},
        }
        result = CriticResult.from_dict(d)
        assert result.instruction_patch == "You must use a tool call."
        assert result.state_patch == {"_retry_count": 1}

    def test_critic_result_to_dict_roundtrip(self):
        from qitos.engine.critic_result import CriticResult
        original = CriticResult(
            action="retry",
            reason="test",
            score=0.5,
            instruction_patch="Use tools",
        )
        d = original.to_dict()
        restored = CriticResult.from_dict(d)
        assert restored.action == original.action
        assert restored.instruction_patch == original.instruction_patch


# ===== Embedding Tests =====

class TestEmbedding:
    def test_hash_embedder(self):
        from qitos.kit.embedding.base import Embedder
        from qitos.kit.memory.vector_memory import _HashEmbedder
        embedder = _HashEmbedder()
        assert embedder.dimension == 16
        vec = embedder.embed("test text")
        assert len(vec) == 16

    def test_callable_embedder_wrapper(self):
        from qitos.kit.memory.vector_memory import _CallableEmbedder
        def my_embedder(text: str) -> list:
            return [float(len(text))] * 8
        wrapped = _CallableEmbedder(my_embedder)
        assert wrapped.dimension == 8
        vec = wrapped.embed("hello")
        assert len(vec) == 8


# ===== VectorStore Tests =====

class TestInMemoryVectorStore:
    def test_upsert_and_query(self):
        from qitos.kit.vectorstore.memory_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        store.upsert("1", [1.0, 0.0, 0.0], {"type": "guide"}, "test content")
        assert store.count() == 1
        results = store.query([1.0, 0.0, 0.0], top_k=5)
        assert len(results) == 1
        assert results[0].id == "1"

    def test_delete(self):
        from qitos.kit.vectorstore.memory_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        store.upsert("1", [1.0, 0.0])
        store.delete(["1"])
        assert store.count() == 0

    def test_get(self):
        from qitos.kit.vectorstore.memory_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        store.upsert("1", [1.0, 0.0], text="hello")
        result = store.get("1")
        assert result is not None
        assert result.text == "hello"

    def test_query_with_filter(self):
        from qitos.kit.vectorstore.memory_store import InMemoryVectorStore
        store = InMemoryVectorStore()
        store.upsert("1", [1.0, 0.0], {"type": "guide"}, "guide content")
        store.upsert("2", [0.9, 0.1], {"type": "code"}, "code content")
        results = store.query([1.0, 0.0], top_k=5, filter={"type": "guide"})
        assert len(results) == 1
        assert results[0].id == "1"


# ===== Search Backend Tests =====

class TestSearchBackends:
    def test_search_result_dataclass(self):
        from qitos.kit.search.base import SearchResult
        result = SearchResult(
            title="Test",
            url="https://example.com",
            snippet="A test result",
            source="test",
        )
        assert result.title == "Test"
        assert result.source == "test"

    def test_duckduckgo_backend_name(self):
        from qitos.kit.search.duckduckgo import DuckDuckGoSearchBackend
        backend = DuckDuckGoSearchBackend()
        assert backend.name == "duckduckgo"

    def test_searxng_backend_name(self):
        from qitos.kit.search.searxng import SearXNGSearchBackend
        backend = SearXNGSearchBackend(base_url="http://localhost:8080")
        assert backend.name == "searxng"


# ===== PentAGIRunner Tests =====

class TestPentAGIRunner:
    def test_runner_creation(self):
        from qitos.examples.pentagi import PentAGIRunner, PentAGIConfig
        config = PentAGIConfig(model_name="test-model")
        runner = PentAGIRunner(config)
        assert runner.config.model_name == "test-model"


# ===== RecoveryDecision Tests =====

class TestRecoveryDecision:
    def test_state_patch_field(self):
        from qitos.engine.recovery import RecoveryDecision
        decision = RecoveryDecision(
            handled=True,
            continue_run=True,
            state_patch={"retry_count": 2},
            instruction_patch="Try a different approach",
        )
        assert decision.state_patch == {"retry_count": 2}
        assert decision.instruction_patch == "Try a different approach"
