"""Integration tests for the QitOS workflow layer.

Tests verify that the integration layer (AgentNode, ToolNode,
SharedMemoryAdapter, QitaTracingLayer) correctly wraps qitos-dag
and uses real qitos APIs.
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

if os.getenv("QITOS_RUN_OPTIONAL_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "optional workflow integration test; set QITOS_RUN_OPTIONAL_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

qitos_dag = pytest.importorskip("qitos_dag", reason="qitos-dag is required for workflow integration tests")

from qitos_dag.schema import EdgeSchema, NodeSchema, WorkflowSchema
from qitos_dag.graph_engine import GraphEngine
from qitos_dag.variable_pool import VariablePool

import qitos_dag.nodes


# ---------- Helper: register QitOS workflow nodes ----------

def _register_qitos_nodes():
    """Import qitos workflow nodes to register them in the global registry."""
    from qitos.workflow.nodes.agent import AgentNode
    from qitos.workflow.nodes.tool import ToolNode
    from qitos.workflow.nodes.human import HumanInputNode


_register_qitos_nodes()


# ---------- 1. Code + IfElse + Code (pure qitos-dag, no LLM) ----------


class TestPureDagWorkflow:
    @pytest.mark.asyncio
    async def test_code_ifelse_code_pipeline(self):
        """Pure qitos-dag nodes work end-to-end without any qitos deps."""
        schema = WorkflowSchema(
            nodes=[
                NodeSchema(id="start", type="start"),
                NodeSchema(
                    id="calc",
                    type="code",
                    data={"code": "def main(inputs): return {'score': 85}"},
                ),
                NodeSchema(
                    id="check",
                    type="if-else",
                    data={
                        "cases": [{
                            "case_id": "case-0",
                            "conditions": [{
                                "variable_selector": ["calc", "score"],
                                "operator": "greater_than",
                                "value": 80,
                            }],
                        }]
                    },
                ),
                NodeSchema(
                    id="high",
                    type="code",
                    data={"code": "result = {'level': 'high'}"},
                ),
                NodeSchema(
                    id="low",
                    type="code",
                    data={"code": "result = {'level': 'low'}"},
                ),
                NodeSchema(id="end", type="end"),
            ],
            edges=[
                EdgeSchema(source="start", target="calc"),
                EdgeSchema(source="calc", target="check"),
                EdgeSchema(source="check", target="high", source_handle="case-0"),
                EdgeSchema(source="check", target="low", source_handle="else"),
                EdgeSchema(source="high", target="end"),
                EdgeSchema(source="low", target="end"),
            ],
        )
        engine = GraphEngine(schema=schema)
        engine.compile()
        result = await engine.run(inputs={})

        assert result.succeeded
        assert "high" in result.node_results
        assert "low" in result.skipped_nodes


# ---------- 2. AgentNode with mock LLM ----------


class TestAgentNode:
    def test_agent_node_registered(self):
        """AgentNode is registered in the node registry."""
        from qitos_dag.node import _NODE_REGISTRY

        assert "agent" in _NODE_REGISTRY

    @pytest.mark.asyncio
    async def test_agent_node_delegate_with_registry(self):
        """AgentNode delegates to a pre-registered agent via Engine."""
        from qitos.workflow.nodes.agent import AgentNode, _WorkflowAgent
        from qitos_dag.node import NodeConfig, create_node
        from qitos.core.agent_spec import AgentSpec, AgentRegistry
        from qitos.core.state import StateSchema

        # Create a mock agent
        mock_agent = _WorkflowAgent(
            system_prompt="You are a test agent.",
            prompt_template="Process: {task}",
            llm=None,
        )

        # Set up registry
        spec = AgentSpec(
            name="test_agent",
            description="Test agent",
            agent=mock_agent,
        )
        registry = AgentRegistry()
        registry.register(spec)

        # Create AgentNode
        config = NodeConfig(
            id="agent1",
            type="agent",
            data={
                "agent_name": "test_agent",
                "task_template": "Analyze this data",
                "max_steps": 1,
                "_agent_registry": registry,
            },
        )
        node = create_node(config)

        # Mock Engine.run to avoid needing a real LLM
        from qitos.engine.states import RuntimeBudget

        mock_engine_result = MagicMock()
        mock_engine_result.state = StateSchema(
            task="Analyze this data", max_steps=1
        )
        mock_engine_result.state.final_result = "Analysis complete"
        mock_engine_result.state.stop_reason = "max_steps"
        mock_engine_result.state.current_step = 1

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_engine_result
            pool = VariablePool()
            result = await node.run(inputs={}, pool=pool)

        assert result["result"] == "Analysis complete"
        assert result["stop_reason"] == "max_steps"
        assert result["steps"] == 1

        # Verify Engine was called with RuntimeBudget, not int
        MockEngine.assert_called_once()
        call_kwargs = MockEngine.call_args
        budget_arg = call_kwargs.kwargs.get("budget") or call_kwargs[1].get("budget")
        assert isinstance(budget_arg, RuntimeBudget)
        assert budget_arg.max_steps == 1

    @pytest.mark.asyncio
    async def test_agent_node_auto_created_fallback(self):
        """AgentNode falls back to _WorkflowAgent when agent not in registry."""
        from qitos.workflow.nodes.agent import AgentNode, _WorkflowAgent
        from qitos_dag.node import NodeConfig, create_node
        from qitos.core.agent_spec import AgentRegistry
        from qitos.core.state import StateSchema

        # Empty registry — agent_name won't be found
        registry = AgentRegistry()

        config = NodeConfig(
            id="agent2",
            type="agent",
            data={
                "agent_name": "nonexistent",
                "prompt_template": "Summarize: {task}",
                "system_prompt": "You are a summarizer.",
                "max_steps": 2,
                "_agent_registry": registry,
            },
        )
        node = create_node(config)

        mock_engine_result = MagicMock()
        mock_engine_result.state = StateSchema(task="test", max_steps=2)
        mock_engine_result.state.final_result = "Summary"
        mock_engine_result.state.stop_reason = "max_steps"
        mock_engine_result.state.current_step = 2

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_engine_result
            pool = VariablePool()
            result = await node.run(inputs={}, pool=pool)

        assert result["result"] == "Summary"

        # Verify the agent passed to Engine is a _WorkflowAgent
        agent_arg = MockEngine.call_args.kwargs.get("agent") or MockEngine.call_args[1].get("agent")
        assert isinstance(agent_arg, _WorkflowAgent)
        assert agent_arg._system_prompt == "You are a summarizer."

    @pytest.mark.asyncio
    async def test_agent_node_no_registry_auto_create(self):
        """AgentNode auto-creates _WorkflowAgent when no registry provided."""
        from qitos.workflow.nodes.agent import _WorkflowAgent
        from qitos_dag.node import NodeConfig, create_node
        from qitos.core.state import StateSchema

        config = NodeConfig(
            id="agent3",
            type="agent",
            data={
                "prompt_template": "Process: {task}",
                "system_prompt": "Auto agent",
                "max_steps": 1,
            },
        )
        node = create_node(config)

        mock_engine_result = MagicMock()
        mock_engine_result.state = StateSchema(task="test", max_steps=1)
        mock_engine_result.state.final_result = "Done"
        mock_engine_result.state.stop_reason = "final"
        mock_engine_result.state.current_step = 1

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_engine_result
            pool = VariablePool()
            result = await node.run(inputs={"task": "hello"}, pool=pool)

        assert result["result"] == "Done"

    def test_workflow_agent_init_state(self):
        """_WorkflowAgent.init_state returns StateSchema with correct max_steps."""
        from qitos.workflow.nodes.agent import _WorkflowAgent

        agent = _WorkflowAgent(system_prompt="test")
        state = agent.init_state("my task", max_steps=5)
        assert state.task == "my task"
        assert state.max_steps == 5

    def test_workflow_agent_prepare(self):
        """_WorkflowAgent.prepare resolves prompt template."""
        from qitos.workflow.nodes.agent import _WorkflowAgent

        agent = _WorkflowAgent(prompt_template="Summarize: {task}")
        state = MagicMock()
        state.task = "some content"
        result = agent.prepare(state)
        assert result == "Summarize: some content"

    def test_workflow_agent_reduce(self):
        """_WorkflowAgent.reduce captures final_answer on final decisions."""
        from qitos.workflow.nodes.agent import _WorkflowAgent
        from qitos.core.state import StateSchema
        from qitos.core.decision import Decision

        agent = _WorkflowAgent()
        state = StateSchema(task="test", max_steps=1)
        decision = Decision.final(answer="the answer")
        new_state = agent.reduce(state, None, decision)
        assert new_state.final_result == "the answer"

    def test_workflow_agent_reduce_non_final(self):
        """_WorkflowAgent.reduce does not set final_result on non-final decisions."""
        from qitos.workflow.nodes.agent import _WorkflowAgent
        from qitos.core.state import StateSchema
        from qitos.core.decision import Decision

        agent = _WorkflowAgent()
        state = StateSchema(task="test", max_steps=1)
        decision = Decision.act(actions=[])
        new_state = agent.reduce(state, None, decision)
        assert new_state.final_result is None


# ---------- 3. ToolNode with mock ToolRegistry ----------


class TestToolNode:
    @pytest.mark.asyncio
    async def test_tool_node_uses_registry_call(self):
        """ToolNode calls tool_registry.call() instead of tool.execute()."""
        from qitos_dag.node import NodeConfig, create_node

        mock_registry = MagicMock()
        mock_registry.call.return_value = {"status": "ok", "data": 42}

        config = NodeConfig(
            id="tool1",
            type="tool",
            data={
                "tool_name": "calculator",
                "tool_args": {"expression": "2+2"},
                "_tool_registry": mock_registry,
            },
        )
        node = create_node(config)
        pool = VariablePool()
        result = await node.run(inputs={}, pool=pool)

        # Verify tool_registry.call was used, not tool.execute
        mock_registry.call.assert_called_once_with(
            "calculator",
            runtime_context={},
            expression="2+2",
        )
        assert result == {"status": "ok", "data": 42}

    @pytest.mark.asyncio
    async def test_tool_node_template_resolution(self):
        """ToolNode resolves template references in args."""
        from qitos_dag.node import NodeConfig, create_node

        mock_registry = MagicMock()
        mock_registry.call.return_value = "result"

        config = NodeConfig(
            id="tool2",
            type="tool",
            data={
                "tool_name": "search",
                "tool_args": {"query": "{{#start.keyword#}}"},
                "_tool_registry": mock_registry,
            },
        )
        node = create_node(config)
        pool = VariablePool()
        pool.write("start", "keyword", "test query")

        result = await node.run(inputs={}, pool=pool)

        mock_registry.call.assert_called_once_with(
            "search",
            runtime_context={},
            query="test query",
        )

    @pytest.mark.asyncio
    async def test_tool_node_no_registry(self):
        """ToolNode returns error when no registry available."""
        from qitos_dag.node import NodeConfig, create_node

        config = NodeConfig(
            id="tool3",
            type="tool",
            data={"tool_name": "missing"},
        )
        node = create_node(config)
        pool = VariablePool()
        result = await node.run(inputs={}, pool=pool)

        assert "error" in result

    @pytest.mark.asyncio
    async def test_tool_node_registry_raises_value_error(self):
        """ToolNode catches ValueError from tool_registry.call()."""
        from qitos_dag.node import NodeConfig, create_node

        mock_registry = MagicMock()
        mock_registry.call.side_effect = ValueError("Tool 'bad' not found")

        config = NodeConfig(
            id="tool4",
            type="tool",
            data={
                "tool_name": "bad",
                "_tool_registry": mock_registry,
            },
        )
        node = create_node(config)
        pool = VariablePool()
        result = await node.run(inputs={}, pool=pool)

        assert "error" in result
        assert "not found" in result["error"]


# ---------- 4. SharedMemoryAdapter ----------


class TestSharedMemoryAdapter:
    def test_sync_to_shared_memory(self):
        """Adapter writes VariablePool contents via SharedMemoryNamespace."""
        from qitos.workflow.adapter import SharedMemoryAdapter

        pool = VariablePool()
        pool.write("node1", "result", "hello")
        pool.write("node1", "count", 5)

        mock_memory = MagicMock()
        adapter = SharedMemoryAdapter(pool, mock_memory)

        with patch("qitos.core.shared_memory.SharedMemoryNamespace") as MockNS:
            mock_ns = MagicMock()
            MockNS.return_value = mock_ns

            adapter.sync_to_shared_memory(namespace="workflow")

            MockNS.assert_called_once_with(mock_memory, "workflow")
            # Should have written both variables
            assert mock_ns.write.call_count == 2
            write_calls = {c[0][0]: c[0][1] for c in mock_ns.write.call_args_list}
            assert write_calls["node1.result"] == "hello"
            assert write_calls["node1.count"] == 5

    def test_sync_from_shared_memory(self):
        """Adapter reads SharedMemoryNamespace contents into VariablePool."""
        from qitos.workflow.adapter import SharedMemoryAdapter

        pool = VariablePool()
        mock_memory = MagicMock()
        adapter = SharedMemoryAdapter(pool, mock_memory)

        with patch("qitos.core.shared_memory.SharedMemoryNamespace") as MockNS:
            mock_ns = MagicMock()
            mock_ns.list_keys.return_value = ["node1.result", "node2.score"]
            mock_ns.read.side_effect = lambda key: {
                "node1.result": "hello",
                "node2.score": 99,
            }[key]
            MockNS.return_value = mock_ns

            adapter.sync_from_shared_memory(namespace="workflow")

            MockNS.assert_called_once_with(mock_memory, "workflow")
            assert pool.read(("node1", "result")) == "hello"
            assert pool.read(("node2", "score")) == 99

    def test_sync_no_shared_memory(self):
        """Adapter no-ops when shared_memory is None."""
        from qitos.workflow.adapter import SharedMemoryAdapter

        pool = VariablePool()
        adapter = SharedMemoryAdapter(pool, shared_memory=None)

        # Should not raise
        adapter.sync_to_shared_memory()
        adapter.sync_from_shared_memory()

    def test_write_handoff_payload_with_memory(self):
        """Handoff payload uses SharedMemoryNamespace."""
        from qitos.workflow.adapter import SharedMemoryAdapter

        pool = VariablePool()
        mock_memory = MagicMock()
        adapter = SharedMemoryAdapter(pool, mock_memory)

        with patch("qitos.core.shared_memory.SharedMemoryNamespace") as MockNS:
            mock_ns = MagicMock()
            MockNS.return_value = mock_ns

            adapter.write_handoff_payload("agent_x", {"task": "do stuff"})

            MockNS.assert_called_once_with(mock_memory, "agent_x")
            mock_ns.write.assert_called_once_with("task", "do stuff")

    def test_write_handoff_payload_fallback(self):
        """Handoff payload falls back to conversation variables when no memory."""
        from qitos.workflow.adapter import SharedMemoryAdapter

        pool = VariablePool()
        adapter = SharedMemoryAdapter(pool, shared_memory=None)

        adapter.write_handoff_payload("agent_x", {"task": "do stuff"})

        val = pool.read_optional(("conversation", "_handoff_agent_x_task"))
        assert val == "do stuff"


# ---------- 5. QitaTracingLayer ----------


class TestQitaTracingLayer:
    def test_tracing_lifecycle(self):
        """QitaTracingLayer creates trace and spans via correct API."""
        from qitos.workflow.layers import QitaTracingLayer
        from qitos_dag.schema import WorkflowSchema

        # Mock tracing provider and trace object
        mock_span = MagicMock()
        mock_trace = MagicMock()
        mock_trace.create_span.return_value = mock_span

        mock_provider = MagicMock()
        mock_provider.create_trace.return_value = mock_trace

        layer = QitaTracingLayer(tracing_provider=mock_provider, graph_id="g1")

        # Create a minimal schema
        schema = WorkflowSchema(
            nodes=[NodeSchema(id="n1", type="code")],
            edges=[],
        )

        # Start graph
        layer.on_graph_start(schema, {})
        mock_provider.create_trace.assert_called_once()
        mock_trace.__enter__.assert_called_once()

        # Start node
        mock_node = MagicMock()
        mock_node.id = "n1"
        mock_node.node_type = "code"

        with patch("qitos.tracing.models.SpanType"), \
             patch("qitos.tracing.models.CustomSpanData") as MockSpanData:
            layer.on_node_run_start(mock_node)
            mock_trace.create_span.assert_called_once()
            mock_span.start.assert_called_once()

        # End node
        layer.on_node_run_end(mock_node)
        mock_span.finish.assert_called_once_with(error=None)

        # End graph
        layer.on_graph_end()
        mock_trace.__exit__.assert_called_once()

    def test_tracing_with_error(self):
        """QitaTracingLayer passes error to span.finish and trace.__exit__."""
        from qitos.workflow.layers import QitaTracingLayer

        mock_span = MagicMock()
        mock_trace = MagicMock()
        mock_trace.create_span.return_value = mock_span

        mock_provider = MagicMock()
        mock_provider.create_trace.return_value = mock_trace

        layer = QitaTracingLayer(tracing_provider=mock_provider)

        schema = WorkflowSchema(nodes=[], edges=[])
        layer.on_graph_start(schema, {})

        mock_node = MagicMock()
        mock_node.id = "n1"
        mock_node.node_type = "code"

        with patch("qitos.tracing.models.SpanType"), \
             patch("qitos.tracing.models.CustomSpanData"):
            layer.on_node_run_start(mock_node)

        error = RuntimeError("node failed")
        layer.on_node_run_end(mock_node, error=error)
        mock_span.finish.assert_called_once_with(error="node failed")

        layer.on_graph_end(error=error)

    def test_tracing_no_provider(self):
        """QitaTracingLayer no-ops when no provider."""
        from qitos.workflow.layers import QitaTracingLayer

        layer = QitaTracingLayer(tracing_provider=None)

        # Should not raise
        schema = WorkflowSchema(nodes=[], edges=[])
        layer.on_graph_start(schema, {})
        layer.on_node_run_start(MagicMock())
        layer.on_node_run_end(MagicMock())
        layer.on_graph_end()


# ---------- 6. QitosNodeFactory ----------


class TestQitosNodeFactory:
    def test_factory_injects_registry(self):
        """QitosNodeFactory injects _tool_registry and _agent_registry."""
        from qitos.workflow.factory import QitosNodeFactory

        mock_tool_registry = MagicMock()
        mock_agent_registry = MagicMock()

        factory = QitosNodeFactory(
            tool_registry=mock_tool_registry,
            agent_registry=mock_agent_registry,
        )

        # Create tool node
        tool_schema = NodeSchema(
            id="t1", type="tool", data={"tool_name": "search"}
        )
        tool_node = factory.create(tool_schema)
        assert tool_node.config.data["_tool_registry"] is mock_tool_registry

        # Create agent node
        agent_schema = NodeSchema(
            id="a1", type="agent", data={"prompt_template": "test"}
        )
        agent_node = factory.create(agent_schema)
        assert agent_node.config.data["_agent_registry"] is mock_agent_registry

    def test_factory_no_llm_import(self):
        """Factory does not import LLMNode (deleted)."""
        import qitos.workflow.factory as factory_mod
        import inspect

        source = inspect.getsource(factory_mod)
        assert "LLMNode" not in source

    def test_factory_injects_shared_memory_and_llm(self):
        """QitosNodeFactory injects _shared_memory, _llm, _tracing_provider, _hooks into agent nodes."""
        from qitos.workflow.factory import QitosNodeFactory

        mock_sm = MagicMock()
        mock_llm = MagicMock()
        mock_tp = MagicMock()
        mock_hooks = [MagicMock()]

        factory = QitosNodeFactory(
            agent_registry=MagicMock(),
            shared_memory=mock_sm,
            llm=mock_llm,
            tracing_provider=mock_tp,
            hooks=mock_hooks,
        )

        agent_schema = NodeSchema(
            id="a1", type="agent", data={"prompt_template": "test"}
        )
        node = factory.create(agent_schema)

        assert node.config.data["_shared_memory"] is mock_sm
        assert node.config.data["_llm"] is mock_llm
        assert node.config.data["_tracing_provider"] is mock_tp
        assert node.config.data["_hooks"] == mock_hooks


# ---------- 7. Phase 1: Full Injection ----------


class TestFullInjection:
    @pytest.mark.asyncio
    async def test_agent_node_delegate_with_full_injection(self):
        """AgentNode._delegate() passes shared_memory, tracing_provider, agent_registry, hooks to Engine."""
        from qitos.workflow.nodes.agent import _WorkflowAgent
        from qitos_dag.node import NodeConfig, create_node
        from qitos.core.agent_spec import AgentSpec, AgentRegistry
        from qitos.core.state import StateSchema

        mock_agent = _WorkflowAgent(system_prompt="test")
        spec = AgentSpec(name="injected_agent", description="test", agent=mock_agent)
        registry = AgentRegistry()
        registry.register(spec)

        mock_sm = MagicMock()
        mock_tp = MagicMock()
        mock_hooks = [MagicMock()]

        config = NodeConfig(
            id="agent_inj",
            type="agent",
            data={
                "agent_name": "injected_agent",
                "max_steps": 3,
                "_agent_registry": registry,
                "_shared_memory": mock_sm,
                "_tracing_provider": mock_tp,
                "_hooks": mock_hooks,
            },
        )
        node = create_node(config)

        mock_engine_result = MagicMock()
        mock_engine_result.state = StateSchema(task="test", max_steps=3)
        mock_engine_result.state.final_result = "done"
        mock_engine_result.state.stop_reason = "final"
        mock_engine_result.state.current_step = 1
        mock_engine_result._runtime_history = None

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_engine_result
            MockEngine.return_value._runtime_history = None
            pool = VariablePool()
            result = await node.run(inputs={}, pool=pool)

        assert result["result"] == "done"

        # Verify Engine received full injection
        call_kwargs = MockEngine.call_args.kwargs
        assert call_kwargs["shared_memory"] is mock_sm
        assert call_kwargs["tracing_provider"] is mock_tp
        assert call_kwargs["agent_registry"] is registry
        assert call_kwargs["hooks"] == mock_hooks

    @pytest.mark.asyncio
    async def test_agent_node_writes_result_to_pool(self):
        """AgentNode writes result fields to VariablePool for downstream nodes."""
        from qitos.workflow.nodes.agent import _WorkflowAgent
        from qitos_dag.node import NodeConfig, create_node
        from qitos.core.state import StateSchema

        config = NodeConfig(
            id="my_agent",
            type="agent",
            data={"prompt_template": "test", "max_steps": 1},
        )
        node = create_node(config)

        mock_engine_result = MagicMock()
        mock_engine_result.state = StateSchema(task="test", max_steps=1)
        mock_engine_result.state.final_result = "the answer"
        mock_engine_result.state.stop_reason = "final"
        mock_engine_result.state.current_step = 2
        mock_engine_result._runtime_history = None

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_engine_result
            MockEngine.return_value._runtime_history = None
            pool = VariablePool()
            result = await node.run(inputs={}, pool=pool)

        # Verify pool has the result fields
        assert pool.read(("my_agent", "result")) == "the answer"
        assert pool.read(("my_agent", "stop_reason")) == "final"
        assert pool.read(("my_agent", "steps")) == 2


# ---------- 8. Phase 3: State Bridge ----------


class TestStateBridge:
    def test_sync_engine_result_static_method(self):
        """SharedMemoryAdapter.sync_engine_result writes state fields to pool."""
        from qitos.workflow.adapter import SharedMemoryAdapter
        from qitos.core.state import StateSchema

        pool = VariablePool()
        state = StateSchema(task="test", max_steps=5)
        state.final_result = "42"
        state.stop_reason = "final"
        state.current_step = 3

        mock_result = MagicMock()
        mock_result.state = state

        SharedMemoryAdapter.sync_engine_result(pool, "agent1", mock_result)

        assert pool.read(("agent1", "result")) == "42"
        assert pool.read(("agent1", "stop_reason")) == "final"
        assert pool.read(("agent1", "steps")) == 3

    @pytest.mark.asyncio
    async def test_agent_node_syncs_shared_memory_after_run(self):
        """AgentNode syncs SharedMemory back to pool after Engine.run()."""
        from qitos.workflow.nodes.agent import _WorkflowAgent
        from qitos_dag.node import NodeConfig, create_node
        from qitos.core.state import StateSchema

        mock_sm = MagicMock()

        config = NodeConfig(
            id="sm_agent",
            type="agent",
            data={
                "prompt_template": "test",
                "max_steps": 1,
                "_shared_memory": mock_sm,
            },
        )
        node = create_node(config)

        mock_engine_result = MagicMock()
        mock_engine_result.state = StateSchema(task="test", max_steps=1)
        mock_engine_result.state.final_result = "ok"
        mock_engine_result.state.stop_reason = "final"
        mock_engine_result.state.current_step = 1
        mock_engine_result._runtime_history = None

        with patch("qitos.engine.engine.Engine") as MockEngine, \
             patch("qitos.workflow.adapter.SharedMemoryAdapter") as MockAdapter:
            MockEngine.return_value.run.return_value = mock_engine_result
            MockEngine.return_value._runtime_history = None
            mock_adapter_instance = MagicMock()
            MockAdapter.return_value = mock_adapter_instance

            pool = VariablePool()
            result = await node.run(inputs={}, pool=pool)

        # Verify adapter was created and sync_from_shared_memory called
        MockAdapter.assert_called_once_with(pool, mock_sm)
        mock_adapter_instance.sync_from_shared_memory.assert_called_once()

    def test_runner_passes_shared_memory_to_factory(self):
        """WorkflowRunner passes shared_memory to QitosNodeFactory."""
        from qitos.workflow.runner import WorkflowRunner

        mock_sm = MagicMock()
        mock_llm = MagicMock()

        runner = WorkflowRunner(shared_memory=mock_sm, llm=mock_llm)

        assert runner.factory.shared_memory is mock_sm
        assert runner.factory.llm is mock_llm

    @pytest.mark.asyncio
    async def test_conversation_history_passthrough(self):
        """AgentNode injects conversation history from pool into agent before run."""
        from qitos.workflow.nodes.agent import _WorkflowAgent
        from qitos_dag.node import NodeConfig, create_node
        from qitos.core.state import StateSchema

        config = NodeConfig(
            id="hist_agent",
            type="agent",
            data={"prompt_template": "test", "max_steps": 1},
        )
        node = create_node(config)

        # Pre-populate conversation history in pool
        pool = VariablePool()
        pool.set_conversation_variable("_conversation_history", [{"role": "user", "content": "hello"}])

        mock_engine_result = MagicMock()
        mock_engine_result.state = StateSchema(task="test", max_steps=1)
        mock_engine_result.state.final_result = "ok"
        mock_engine_result.state.stop_reason = "final"
        mock_engine_result.state.current_step = 1
        mock_engine_result._runtime_history = MagicMock()
        mock_engine_result._runtime_history._items = [{"role": "assistant", "content": "hi"}]

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_engine_result
            MockEngine.return_value._runtime_history = mock_engine_result._runtime_history

            result = await node.run(inputs={}, pool=pool)

        # Verify conversation history was updated in pool after run
        history = pool.read_optional(("conversation", "_conversation_history"))
        assert history is not None
        assert len(history) == 1


# ---------- 9. Phase 2: Event Bridge ----------


class TestEventBridge:
    def test_engine_to_dag_hook_forwards_step_events(self):
        """EngineToDagHook converts Engine steps to WorkflowEvents."""
        from qitos.workflow.event_bridge import EngineToDagHook
        from qitos_dag.events import EventType

        emitted = []
        hook = EngineToDagHook(emit_callback=lambda e: emitted.append(e), node_id="agent1")
        engine_hook = hook.as_engine_hook()

        # Simulate on_after_step
        mock_ctx = MagicMock()
        mock_ctx.step_id = 3
        mock_ctx.phase = MagicMock()
        mock_ctx.phase.value = "DECIDE"

        engine_hook.on_after_step(mock_ctx, MagicMock())

        assert len(emitted) == 1
        assert emitted[0].event_type == EventType.NODE_RUN_SUCCEEDED
        assert emitted[0].data["_engine_event"] is True
        assert emitted[0].data["step_id"] == 3
        assert emitted[0].data["phase"] == "DECIDE"

    def test_engine_to_dag_hook_forwards_run_end(self):
        """EngineToDagHook converts run_end to WorkflowEvent."""
        from qitos.workflow.event_bridge import EngineToDagHook
        from qitos_dag.events import EventType
        from qitos.core.state import StateSchema

        emitted = []
        hook = EngineToDagHook(emit_callback=lambda e: emitted.append(e), node_id="agent1")
        engine_hook = hook.as_engine_hook()

        mock_result = MagicMock()
        mock_result.state = StateSchema(task="test", max_steps=1)
        mock_result.state.final_result = "done"
        mock_result.state.stop_reason = "final"

        engine_hook.on_run_end(mock_result, MagicMock())

        assert len(emitted) == 1
        assert emitted[0].data["event"] == "engine_run_end"
        assert emitted[0].data["final_result"] == "done"

    def test_dag_to_engine_layer_forwards_events(self):
        """DagToEngineLayer converts WorkflowEvents to EngineEvents."""
        from qitos.workflow.event_bridge import DagToEngineLayer
        from qitos_dag.events import NodeRunStartedEvent, EventType
        from qitos.engine.events import EngineEventType

        mock_stream = MagicMock()
        layer = DagToEngineLayer(event_stream=mock_stream)

        event = NodeRunStartedEvent(
            graph_id="g1",
            data={"node_id": "n1"},
        )
        layer.on_event(event)

        mock_stream.emit.assert_called_once()
        engine_event = mock_stream.emit.call_args[0][0]
        assert engine_event.event_type == EngineEventType.STEP_START
        assert engine_event.agent_id == "n1"
        assert engine_event.payload["_dag_event"] is True

    def test_tracing_layer_creates_child_spans_for_engine_events(self):
        """QitaTracingLayer creates child spans for engine step events."""
        from qitos.workflow.layers import QitaTracingLayer

        mock_parent_span = MagicMock()
        mock_child_span = MagicMock()
        mock_trace = MagicMock()
        mock_trace.create_span.return_value = mock_child_span

        mock_provider = MagicMock()
        mock_provider.create_trace.return_value = mock_trace

        layer = QitaTracingLayer(tracing_provider=mock_provider)

        schema = WorkflowSchema(nodes=[], edges=[])
        layer.on_graph_start(schema, {})

        # Simulate a node running with a span
        mock_node = MagicMock()
        mock_node.id = "agent1"
        mock_node.node_type = "agent"

        with patch("qitos.tracing.models.SpanType"), \
             patch("qitos.tracing.models.CustomSpanData"):
            layer.on_node_run_start(mock_node)
        layer._spans["agent1"] = mock_parent_span

        # Simulate an engine step event
        engine_event = MagicMock()
        engine_event.data = {"_engine_event": True, "node_id": "agent1", "step_id": 2}

        with patch("qitos.tracing.models.SpanType"), \
             patch("qitos.tracing.models.CustomSpanData"):
            layer.on_event(engine_event)

        # Child span should be created under the parent
        mock_trace.create_span.assert_called()
        mock_child_span.start.assert_called()
        mock_child_span.finish.assert_called()


# ---------- 10. Phase 4: WorkflowTool + WorkflowRegistry ----------


class TestWorkflowRegistry:
    def test_register_and_resolve(self):
        """WorkflowRegistry stores and retrieves specs."""
        from qitos.workflow.registry import WorkflowRegistry, WorkflowSpec

        registry = WorkflowRegistry()
        schema = WorkflowSchema(nodes=[], edges=[])
        spec = WorkflowSpec(name="test_wf", description="A test workflow", schema=schema)

        registry.register(spec)
        resolved = registry.resolve("test_wf")
        assert resolved.name == "test_wf"
        assert resolved.schema is schema

    def test_resolve_raises_key_error(self):
        """WorkflowRegistry.resolve raises KeyError on missing name."""
        from qitos.workflow.registry import WorkflowRegistry

        registry = WorkflowRegistry()
        with pytest.raises(KeyError):
            registry.resolve("missing")

    def test_list_available(self):
        """WorkflowRegistry.list_available returns all specs."""
        from qitos.workflow.registry import WorkflowRegistry, WorkflowSpec

        registry = WorkflowRegistry()
        registry.register(WorkflowSpec(name="a", description="A", schema=WorkflowSchema(nodes=[], edges=[])))
        registry.register(WorkflowSpec(name="b", description="B", schema=WorkflowSchema(nodes=[], edges=[])))

        available = registry.list_available()
        assert len(available) == 2
        assert {s.name for s in available} == {"a", "b"}

    def test_get_workflow_tools(self):
        """WorkflowRegistry.get_workflow_tools creates WorkflowTool per spec."""
        from qitos.workflow.registry import WorkflowRegistry, WorkflowSpec
        from qitos.kit.tool.workflow_tool import WorkflowTool

        registry = WorkflowRegistry()
        registry.register(WorkflowSpec(name="my_wf", description="Test", schema=WorkflowSchema(nodes=[], edges=[])))

        mock_runner = MagicMock()
        tools = registry.get_workflow_tools(runner=mock_runner)
        assert len(tools) == 1
        assert isinstance(tools[0], WorkflowTool)
        assert "my_wf" in tools[0].name


class TestWorkflowTool:
    def test_workflow_tool_name_and_description(self):
        """WorkflowTool has correct name and description."""
        from qitos.kit.tool.workflow_tool import WorkflowTool
        from qitos.workflow.registry import WorkflowSpec

        spec = WorkflowSpec(name="research", description="Research workflow", schema=WorkflowSchema(nodes=[], edges=[]))
        mock_runner = MagicMock()
        tool = WorkflowTool(runner=mock_runner, spec=spec)

        assert tool.name == "run_workflow_research"
        assert tool.workflow_spec.description == "Research workflow"

    def test_workflow_tool_executes_dag(self):
        """WorkflowTool calls runner.run_sync with correct schema and inputs."""
        from qitos.kit.tool.workflow_tool import WorkflowTool
        from qitos.workflow.registry import WorkflowSpec

        schema = WorkflowSchema(nodes=[], edges=[])
        spec = WorkflowSpec(name="test_wf", description="Test", schema=schema, default_inputs={"key": "default"})

        mock_runner = MagicMock()
        mock_result = MagicMock()
        mock_result.succeeded = True
        mock_result.node_results = {"n1": {"output": 42}}
        mock_result.elapsed_ms = 100.5
        mock_runner.run_sync.return_value = mock_result

        tool = WorkflowTool(runner=mock_runner, spec=spec)
        result = tool.execute({"inputs": {"extra": "data"}})

        mock_runner.run_sync.assert_called_once()
        call_args = mock_runner.run_sync.call_args
        assert call_args[0][0] is schema
        # Inputs should merge default + provided
        inputs = call_args[1].get("inputs") or call_args[0][1]
        assert inputs["key"] == "default"
        assert inputs["extra"] == "data"

        assert result["status"] == "succeeded"
        assert result["node_results"]["n1"]["output"] == 42

    def test_workflow_tool_handles_failure(self):
        """WorkflowTool returns failed status when DAG fails."""
        from qitos.kit.tool.workflow_tool import WorkflowTool
        from qitos.workflow.registry import WorkflowSpec

        schema = WorkflowSchema(nodes=[], edges=[])
        spec = WorkflowSpec(name="fail_wf", description="Fails", schema=schema)

        mock_runner = MagicMock()
        mock_result = MagicMock()
        mock_result.succeeded = False
        mock_result.error = "No root nodes"
        mock_result.node_results = {}
        mock_runner.run_sync.return_value = mock_result

        tool = WorkflowTool(runner=mock_runner, spec=spec)
        result = tool.execute({})

        assert result["status"] == "failed"
        assert "No root nodes" in result["error"]


# ---------- 11. Phase 5: Multi-agent Pattern DAG Nodes ----------


class TestDebatePattern:
    def test_debate_schema_validates(self):
        """build_debate_schema returns a valid WorkflowSchema."""
        from qitos.workflow.patterns.debate import build_debate_schema, DebateDagConfig

        config = DebateDagConfig(debaters=["pro", "con"], rounds=2)
        schema = build_debate_schema(config)

        assert schema.title == "Debate Pattern"
        node_ids = {n.id for n in schema.nodes}
        assert "start" in node_ids
        assert "debate_rounds" in node_ids
        assert "moderator" in node_ids
        assert "end" in node_ids

        # Verify edges form a chain
        edge_pairs = [(e.source, e.target) for e in schema.edges]
        assert ("start", "debate_rounds") in edge_pairs
        assert ("debate_rounds", "moderator") in edge_pairs
        assert ("moderator", "end") in edge_pairs

    def test_debate_default_config(self):
        """Default debate config has 2 debaters, 3 rounds."""
        from qitos.workflow.patterns.debate import build_debate_schema

        schema = build_debate_schema()
        assert schema.title == "Debate Pattern"

        # Loop node should have count=3
        loop_node = next(n for n in schema.nodes if n.id == "debate_rounds")
        assert loop_node.data["count"] == 3


class TestMoAPattern:
    def test_moa_schema_has_parallel_proposers(self):
        """build_moa_schema creates a ParallelNode for proposers."""
        from qitos.workflow.patterns.moa import build_moa_schema, MoADagConfig

        config = MoADagConfig(proposers=["a", "b", "c"])
        schema = build_moa_schema(config)

        node_ids = {n.id for n in schema.nodes}
        assert "proposers" in node_ids
        assert "aggregator" in node_ids

        # ParallelNode should have 3 branches
        parallel_node = next(n for n in schema.nodes if n.id == "proposers")
        assert len(parallel_node.data["branches"]) == 3

    def test_moa_default_config(self):
        """Default MoA config has 3 proposers."""
        from qitos.workflow.patterns.moa import build_moa_schema

        schema = build_moa_schema()
        parallel_node = next(n for n in schema.nodes if n.id == "proposers")
        assert len(parallel_node.data["branches"]) == 3


class TestManagerWorkerPattern:
    def test_manager_worker_schema_structure(self):
        """build_manager_worker_schema creates manager + iteration worker nodes."""
        from qitos.workflow.patterns.manager_worker import build_manager_worker_schema

        schema = build_manager_worker_schema()

        node_ids = {n.id for n in schema.nodes}
        assert "manager" in node_ids
        assert "set_tasks" in node_ids
        assert "workers" in node_ids

        # Workers should be an iteration node
        workers_node = next(n for n in schema.nodes if n.id == "workers")
        assert workers_node.type == "iteration"


class TestPlannerExecutorPattern:
    def test_planner_executor_schema_structure(self):
        """build_planner_executor_schema creates planner → executor chain."""
        from qitos.workflow.patterns.planner_executor import build_planner_executor_schema

        schema = build_planner_executor_schema()

        node_ids = {n.id for n in schema.nodes}
        assert "planner" in node_ids
        assert "executor" in node_ids

        # Verify linear chain
        edge_pairs = [(e.source, e.target) for e in schema.edges]
        assert ("start", "planner") in edge_pairs
        assert ("planner", "executor") in edge_pairs
        assert ("executor", "end") in edge_pairs

    def test_planner_executor_custom_names(self):
        """Custom planner/executor names are used."""
        from qitos.workflow.patterns.planner_executor import (
            build_planner_executor_schema, PlannerExecutorDagConfig,
        )

        config = PlannerExecutorDagConfig(planner_name="architect", executor_name="builder")
        schema = build_planner_executor_schema(config)

        planner = next(n for n in schema.nodes if n.id == "planner")
        executor = next(n for n in schema.nodes if n.id == "executor")
        assert planner.data["agent_name"] == "architect"
        assert executor.data["agent_name"] == "builder"


class TestPatternSchemaComposable:
    def test_pattern_schemas_have_start_and_end(self):
        """All pattern schemas have start and end nodes."""
        from qitos.workflow.patterns import (
            build_debate_schema, build_moa_schema,
            build_manager_worker_schema, build_planner_executor_schema,
        )

        for builder in [build_debate_schema, build_moa_schema,
                        build_manager_worker_schema, build_planner_executor_schema]:
            schema = builder()
            node_ids = {n.id for n in schema.nodes}
            assert "start" in node_ids, f"{builder.__name__} missing start node"
            assert "end" in node_ids, f"{builder.__name__} missing end node"


# ---------- 12. Phase 6: Deprecation + Exports ----------


class TestDeprecation:
    def test_old_workflow_emits_deprecation_warning(self):
        """Importing qitos.kit.patterns.workflow emits DeprecationWarning."""
        import importlib
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Force re-import
            import qitos.kit.patterns.workflow as wf_mod
            importlib.reload(wf_mod)

            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(dep_warnings) >= 1
            assert "deprecated" in str(dep_warnings[0].message).lower()


class TestExports:
    def test_workflow_package_exports(self):
        """qitos.workflow exports all key classes."""
        from qitos.workflow import (
            QitosNodeFactory,
            WorkflowRunner,
            SharedMemoryAdapter,
            EngineToDagHook,
            DagToEngineLayer,
            WorkflowRegistry,
            WorkflowSpec,
        )

        assert WorkflowRunner is not None
        assert WorkflowRegistry is not None
        assert EngineToDagHook is not None

    def test_workflow_tool_importable(self):
        """WorkflowTool is importable from qitos.kit.tool."""
        from qitos.kit.tool.workflow_tool import WorkflowTool

        assert WorkflowTool is not None
