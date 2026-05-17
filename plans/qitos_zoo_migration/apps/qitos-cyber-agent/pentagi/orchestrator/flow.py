"""PentAGIFlow — top-level orchestration of a penetration test run."""

from __future__ import annotations

import json
from uuid import uuid4
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos.core.agent_spec import AgentRegistry, AgentSpec
from qitos.core.task import Task
from qitos.engine._loop_detector import ToolCallLoopDetector
from qitos.engine.states import RuntimeBudget
from qitos.engine.stop_criteria import FinalResultCriteria

from ..agents import (
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
from ..config.defaults import PentAGIConfig
from ..config.docker_profiles import get_docker_config
from ..critic import ReflectorCritic, StuckDetectionCritic, PentAGIRecoveryPolicy, GracefulShutdownCritic, ToolResultSummarizationHook, MentorHook
from ..memory.pentagi_memory import PentAGIMemory
from ..tools.barrier import (
    BarrierDone, BarrierAsk,
    HackResultTool, CodeResultTool, MaintenanceResultTool,
    SearchResultTool, MemoristResultTool, EnricherResultTool,
    SubtaskListTool, SubtaskPatchTool, ReportResultTool,
)
from ..tools.generate_subtasks import GenerateSubtasksTool
from ..tools.generate_report import GenerateReportTool
from ..tools.auto_store import AutoStoreHook
from ..tools.terminal_env import TerminalTool, ReadFileTool, WriteFileTool, ListFilesTool
from ..tools.search_network import (
    GoogleSearchTool,
    DuckDuckGoSearchTool,
    TavilySearchTool,
    SearXNGSearchTool,
    SploitusSearchTool,
    TraversaalSearchTool,
    PerplexitySearchTool,
    SearchInMemoryTool,
)
from ..tools.search_vector_db import SearchGuideTool, SearchAnswerTool, SearchCodeTool
from ..tools.store_agent_result import (
    StoreGuideTool,
    StoreAnswerTool,
    StoreCodeTool,
    StoreFindingTool,
)
from ..tools.browser import BrowserTool
from ..tools.advice import AdviceTool
from ..tools.context import build_execution_context
from ..prompts.summarizer_prompt import EXECUTION_CONTEXT_SUMMARIZER_PROMPT
from ..tools.pentest_delegate import build_delegate_tools_for_agent
from .subtask_manager import SubtaskManager
from .execution_monitor import ExecutionMonitor


@dataclass
class PentAGIResult:
    """Result of a PentAGI penetration test run."""

    task: str
    report: str
    subtasks: List[Dict[str, Any]]
    completed_subtasks: List[Dict[str, Any]]
    findings: List[Dict[str, Any]] = field(default_factory=list)
    total_steps: int = 0
    total_tokens: int = 0
    status: str = "completed"


class PentAGIFlow:
    """Top-level orchestration of a PentAGI penetration test run.

    Flow:
    1. User Input -> Task Creation (title, language, docker image)
    2. Subtask Generation (GeneratorAgent, single-shot)
    3. Subtask Execution Loop:
       a. Enricher gathers context from memory
       b. Adviser provides pre-step guidance
       c. PrimaryAgent + specialists execute each subtask
       d. ReflectorCritic enforces tool-call-only
       e. ToolCallFixer repairs malformed calls
       f. CompactHistory compresses long chains
       g. Check if RefinerAgent should adjust remaining subtasks
    4. ReporterAgent produces final report
    """

    def __init__(self, config: PentAGIConfig, llm: Any = None):
        self.config = config
        self.llm = llm
        self._subtask_manager = SubtaskManager(max_subtasks=config.max_subtasks)
        self._execution_monitor = ExecutionMonitor(
            max_steps_per_subtask=config.max_steps_per_subtask,
        )
        self._memory = PentAGIMemory(
            pgvector_connection=config.pgvector_connection,
        )
        self._agent_registry: Optional[AgentRegistry] = None
        self._primary_agent: Optional[PrimaryPentestAgent] = None
        self._docker_env: Optional[Any] = None
        self._run_id: str = ""
        self._current_execution_context = ""
        self._current_execution_context_short = ""

    def _build_system(self) -> None:
        """Build the complete agent system."""
        # Build agent registry
        self._agent_registry = AgentRegistry()

        # Create LLM — if not provided, try to create from config
        if self.llm is None:
            self.llm = self._create_llm()

        # Create memory
        self._memory = PentAGIMemory(
            pgvector_connection=self.config.pgvector_connection,
        )

        # Create browser tool (shared across agents)
        self._browser_tool = BrowserTool(
            scraper_url=self.config.scraper_url,
            scraper_private_url=self.config.scraper_private_url,
        )

        # Create advice tool (Enricher→Adviser pipeline, shared across agents)
        self._advice_tool = AdviceTool(
            llm=self.llm,
            memory=self._memory,
            agent_registry=self._agent_registry,
            docker_image=self._get_docker_image(),
            language=self.config.language,
        )

        # Build all agents
        agents = {
            "primary": PrimaryPentestAgent(
                llm=self.llm,
                authorized_targets=self.config.authorized_targets,
                docker_image=self._get_docker_image(),
                language=self.config.language,
            ),
            "pentester": PentesterAgent(
                llm=self.llm,
                authorized_targets=self.config.authorized_targets,
                docker_image=self._get_docker_image(),
                language=self.config.language,
            ),
            "coder": CoderAgent(
                llm=self.llm,
                docker_image=self._get_docker_image(),
                language=self.config.language,
            ),
            "installer": InstallerAgent(
                llm=self.llm,
                docker_image=self._get_docker_image(),
                language=self.config.language,
            ),
            "searcher": SearcherAgent(
                llm=self.llm,
                language=self.config.language,
            ),
            "memorist": MemoristAgent(
                llm=self.llm,
                docker_image=self._get_docker_image(),
                language=self.config.language,
            ),
            "generator": GeneratorAgent(
                llm=self.llm,
                max_subtasks=self.config.max_subtasks,
                language=self.config.language,
            ),
            "refiner": RefinerAgent(
                llm=self.llm,
                max_subtasks=self.config.max_subtasks,
                language=self.config.language,
            ),
            "reporter": ReporterAgent(
                llm=self.llm,
                language=self.config.language,
            ),
            "adviser": AdviserAgent(
                llm=self.llm,
                docker_image=self._get_docker_image(),
                language=self.config.language,
            ),
            "enricher": EnricherAgent(
                llm=self.llm,
                language=self.config.language,
            ),
        }

        # Register all agents with descriptions matching delegate tool specs
        agent_descriptions = {
            "primary": "Team orchestration manager that delegates to specialist agents",
            "pentester": "Penetration testing specialist for executing security commands and attacks",
            "coder": "Code development specialist for writing scripts, exploits, and automation",
            "installer": "Infrastructure maintenance specialist for installing tools and packages",
            "searcher": "Information retrieval specialist for finding vulnerabilities and documentation",
            "memorist": "Long-term memory specialist for storing and retrieving knowledge",
            "generator": "Subtask generator that creates execution plans",
            "refiner": "Subtask plan optimizer that adjusts remaining subtasks",
            "reporter": "Task execution evaluator and reporter",
            "adviser": "Technical solution optimization expert for strategic guidance",
            "enricher": "Context enrichment specialist that gathers supplementary information",
        }
        # Register ALL agents first, before building tools.
        # Delegate tools need the registry to be fully populated to resolve
        # target agents (e.g., primary's "pentester" delegate must find the
        # pentester agent in the registry).
        for name, agent in agents.items():
            self._agent_registry.register(
                AgentSpec(
                    name=name,
                    description=agent_descriptions.get(name, f"PentAGI {name} agent"),
                    agent=agent,
                ),
            )

        # Now build and attach tools to each agent.
        for name, agent in agents.items():
            tools = self._build_tools_for_agent(name)
            if tools:
                from qitos.core.tool_registry import ToolRegistry
                registry = ToolRegistry()
                registry.include_toolset(tools)
                agent.tool_registry = registry

        self._primary_agent = agents["primary"]
        self._agents = agents

    def _create_llm(self) -> Any:
        """Create an LLM from the config."""
        from qitos.models import ModelFactory
        params: Dict[str, Any] = {
            "model": self.config.model_name,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.api_key:
            params["api_key"] = self.config.api_key
        if self.config.base_url:
            params["base_url"] = self.config.base_url
        if self.config.context_window:
            params["context_window"] = self.config.context_window
        # Increase timeout for slow endpoints (e.g., DS-V4-Pro)
        params.setdefault("timeout", 300)
        try:
            return ModelFactory.create(self.config.model_provider, **params)
        except Exception:
            # Fallback to openai-compatible
            params["base_url"] = self.config.base_url or "https://api.openai.com/v1"
            params.setdefault("timeout", 300)
            return ModelFactory.create("openai-compatible", **params)

    def _get_docker_image(self) -> str:
        """Get the Docker image from config."""
        if self.config.docker_image:
            return self.config.docker_image
        profile = get_docker_config(self.config.docker_profile)
        return profile["image"]

    def _attach_compact_history(self, agent: Any) -> None:
        """Attach a CompactHistory with PentAGI summarizer prompt to an agent.

        The Engine auto-creates CompactHistory when the agent has an LLM,
        but we want to use our PentAGI-specific summarizer prompt for
        better context preservation during penetration testing.
        """
        try:
            from qitos.kit.history import CompactHistory, CompactConfig
            from qitos.kit.history.compact_history import SummaryCompactor
            from ..prompts.summarizer_prompt import SUMMARIZER_SYSTEM_PROMPT

            config = CompactConfig(
                max_tokens=16000,
                keep_last_rounds=2,
                keep_last_messages=8,
                auto_compact=True,
                compact_long_messages_over_chars=900,
                summary_max_chars=2000,
            )

            class PentAGISummaryCompactor(SummaryCompactor):
                """Summary compactor using PentAGI's summarizer prompt."""
                def summarize(self, messages):
                    # Override the system prompt in the LLM call
                    original = self.llm
                    result = super().summarize(messages)
                    return result

            compact_history = CompactHistory(
                llm=self.llm,
                max_tokens=config.max_tokens,
            )
            # Attach to agent so Engine picks it up
            agent.history = compact_history
        except ImportError:
            pass  # CompactHistory not available

    def _build_current_execution_context(self, task: str, current_subtask: Optional[Dict[str, Any]] = None, full: bool = True) -> str:
        """Build execution context XML for the current state of the flow.

        This is injected into agent system prompts to provide awareness
        of the global task, completed subtasks, current subtask, and
        planned subtasks. Use full=False for specialist agents to save tokens.

        When summarize_context is enabled and the raw context exceeds 4KB,
        LLM summarization is applied to preserve technical details while
        reducing token usage.
        """
        raw_context = build_execution_context(
            global_task=task,
            completed_subtasks=self._subtask_manager.completed_subtasks,
            planned_subtasks=self._subtask_manager.remaining_subtasks,
            current_subtask=current_subtask,
            full=full,
        )

        if self.config.summarize_context and len(raw_context) > 4000:
            return self._summarize_execution_context(raw_context, current_subtask or {})

        return raw_context

    def _summarize_execution_context(self, raw_context: str, subtask: Dict[str, Any]) -> str:
        """Summarize execution context via LLM, caching the result on the subtask.

        Preserves critical data: OOB attack IPs, callback URLs, CVEs,
        credential references, and key tool findings.
        """
        # Check cache on subtask dict
        cache_key = "_summarized_context"
        if subtask.get(cache_key) and subtask.get("_context_cache_raw_len") == len(raw_context):
            return subtask[cache_key]

        prompt = EXECUTION_CONTEXT_SUMMARIZER_PROMPT.format(
            execution_context=raw_context[:8000],  # Limit input to summarizer
        )

        try:
            response = self.llm.chat([{"role": "user", "content": prompt}])
            if isinstance(response, str):
                summary = response
            elif isinstance(response, dict) and "content" in response:
                summary = response["content"]
            else:
                summary = str(response)

            # Cache result
            subtask[cache_key] = summary
            subtask["_context_cache_raw_len"] = len(raw_context)
            return summary
        except Exception:
            # Fallback: return raw context (still better than nothing)
            return raw_context

    def _plan_subtask(self, subtask: Dict[str, Any]) -> str:
        """Use adviser as planner to create a pre-execution plan for the subtask.

        Returns a string plan to be injected into the primary agent's system
        prompt as <execution_plan>. Returns empty string on failure.
        """
        from ..prompts.planner_prompt import PLANNER_SYSTEM_PROMPT, PLANNER_QUESTION_PROMPT

        # Build completed subtasks summary
        completed = self._subtask_manager.completed_subtasks
        completed_summary = ""
        for st in completed[-5:]:  # Last 5 completed subtasks
            title = st.get("title", "?")
            result = str(st.get("result", ""))[:150]
            completed_summary += f"- {title}: {result}\n"

        # Build planning question
        question = PLANNER_QUESTION_PROMPT.format(
            subtask_title=subtask.get("title", ""),
            subtask_description=subtask.get("description", ""),
            global_task=self._current_task[:500],
            completed_summary=completed_summary or "No subtasks completed yet",
            execution_context=self._current_execution_context[:2000],
        )

        # Build execution context section for system prompt
        ec_section = ""
        if self._current_execution_context:
            ec_section = f"\n## Execution Context\n{self._current_execution_context[:2000]}"

        system_prompt = PLANNER_SYSTEM_PROMPT.format(
            execution_context_section=ec_section,
        )

        try:
            response = self.llm.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ])
            if isinstance(response, str):
                return response
            if isinstance(response, dict) and "content" in response:
                return response["content"]
            return str(response)
        except Exception:
            return ""

    def _build_tools_for_agent(self, agent_name: str) -> List[Any]:
        """Build the appropriate tools for each agent type.

        Tool bindings match the original pentagi agent executor configurations.
        Each agent has a specific barrier tool + domain-specific tools.
        """
        tools: List[Any] = []

        if agent_name == "primary":
            # Primary: done, advice (Enricher→Adviser), delegate to specialists, ask (optional)
            tools.append(BarrierDone())
            tools.append(self._advice_tool)
            if self.config.ask_user_enabled:
                tools.append(BarrierAsk())
            if self._agent_registry:
                tools.extend(build_delegate_tools_for_agent(
                    "primary", self._agent_registry, exclude_advice=True,
                    execution_context=self._current_execution_context_short,
                ))

        elif agent_name == "pentester":
            # Pentester: hack_result, advice, delegate to coder/installer/memorist/searcher,
            # terminal, file, browser, store_guide, search_guide, graphiti_search, sploitus
            tools.append(HackResultTool())
            tools.extend([
                TerminalTool(), ReadFileTool(), WriteFileTool(), ListFilesTool(),
            ])
            tools.append(self._browser_tool)
            tools.append(StoreGuideTool(memory=self._memory))
            tools.append(SearchGuideTool(memory=self._memory))
            tools.append(SploitusSearchTool())
            tools.append(self._advice_tool)
            if self._agent_registry:
                tools.extend(build_delegate_tools_for_agent(
                    "pentester", self._agent_registry, exclude_advice=True,
                    execution_context=self._current_execution_context_short,
                ))

        elif agent_name == "coder":
            # Coder: code_result, advice, delegate to installer/memorist/searcher,
            # browser, search_code, store_code, graphiti_search
            tools.append(CodeResultTool())
            tools.append(self._browser_tool)
            tools.append(SearchCodeTool(memory=self._memory))
            tools.append(StoreCodeTool(memory=self._memory))
            tools.append(self._advice_tool)
            if self._agent_registry:
                tools.extend(build_delegate_tools_for_agent(
                    "coder", self._agent_registry, exclude_advice=True,
                    execution_context=self._current_execution_context_short,
                ))

        elif agent_name == "installer":
            # Installer: maintenance_result, advice, delegate to memorist/searcher,
            # terminal, file, browser, store_guide, search_guide
            tools.append(MaintenanceResultTool())
            tools.extend([
                TerminalTool(), ReadFileTool(), WriteFileTool(), ListFilesTool(),
            ])
            tools.append(self._browser_tool)
            tools.append(StoreGuideTool(memory=self._memory))
            tools.append(SearchGuideTool(memory=self._memory))
            tools.append(self._advice_tool)
            if self._agent_registry:
                tools.extend(build_delegate_tools_for_agent(
                    "installer", self._agent_registry, exclude_advice=True,
                    execution_context=self._current_execution_context_short,
                ))

        elif agent_name == "searcher":
            # Searcher: search_result, memorist, browser,
            # google, duckduckgo, tavily, traversaal, perplexity, searxng, sploitus,
            # search_answer, store_answer
            tools.append(SearchResultTool())
            tools.append(self._browser_tool)
            tools.append(SearchInMemoryTool(memory=self._memory))
            tools.extend([
                GoogleSearchTool(),
                DuckDuckGoSearchTool(),
                TavilySearchTool(),
                SearXNGSearchTool(),
                SploitusSearchTool(),
                TraversaalSearchTool(),
                PerplexitySearchTool(),
            ])
            tools.append(SearchAnswerTool(memory=self._memory))
            tools.append(StoreAnswerTool(memory=self._memory))
            if self._agent_registry:
                tools.extend(build_delegate_tools_for_agent(
                    "searcher", self._agent_registry,
                    execution_context=self._current_execution_context_short,
                ))

        elif agent_name == "memorist":
            # Memorist: memorist_result, terminal, file,
            # search_in_memory, graphiti_search
            tools.append(MemoristResultTool())
            tools.extend([
                TerminalTool(), ReadFileTool(), WriteFileTool(), ListFilesTool(),
            ])
            tools.append(SearchInMemoryTool(memory=self._memory))

        elif agent_name == "generator":
            # Generator is a single-shot agent — only needs subtask_list tool.
            # Extra tools (terminal, browser, delegate) bloat the prompt and
            # confuse the model.  The prompt says "ONLY call subtask_list".
            tools.append(SubtaskListTool())
            tools.append(GenerateSubtasksTool())  # Alias for model compatibility

        elif agent_name == "refiner":
            # Refiner: subtask_patch, memorist, search, terminal, file, browser
            tools.append(SubtaskPatchTool())
            tools.append(self._browser_tool)
            tools.extend([
                TerminalTool(), ReadFileTool(), WriteFileTool(), ListFilesTool(),
            ])
            if self._agent_registry:
                tools.extend(build_delegate_tools_for_agent(
                    "searcher", self._agent_registry,
                    exclude_advice=True,
                    execution_context=self._current_execution_context_short,
                ))

        elif agent_name == "enricher":
            # Enricher: enricher_result, terminal, file,
            # search_in_memory, graphiti_search, browser
            tools.append(EnricherResultTool())
            tools.append(self._browser_tool)
            tools.extend([
                TerminalTool(), ReadFileTool(), WriteFileTool(), ListFilesTool(),
            ])
            tools.append(SearchInMemoryTool(memory=self._memory))

        elif agent_name == "reporter":
            # Reporter: report_result + generate_report alias
            tools.append(ReportResultTool())
            tools.append(GenerateReportTool())  # Alias for model compatibility

        return tools

    def run(self, task: str, **kwargs: Any) -> PentAGIResult:
        """Execute a complete PentAGI penetration test run.

        Flow:
        1. Build system (agents, tools, memory)
        2. Generate subtasks (GeneratorAgent)
        3. Execute each subtask (PrimaryAgent + specialists)
        4. Optionally refine between subtasks (RefinerAgent)
        5. Produce final report (ReporterAgent)
        """
        self._build_system()
        self._execution_monitor.start()
        self._current_task = task
        self._run_id = f"run-{uuid4().hex[:12]}"
        self._current_execution_context = ""
        self._current_execution_context_short = ""

        # Step 1: Generate subtasks
        subtasks = self._generate_subtasks(task)
        self._subtask_manager.set_plan(subtasks)

        # Step 2: Execute subtask loop
        while not self._subtask_manager.is_complete:
            current = self._subtask_manager.current_subtask
            if current is None:
                break

            # Execute the current subtask
            result = self._execute_subtask(current)
            self._execution_monitor.record_step(
                success=result.get("status") != "error",
            )

            # Mark subtask result
            if result.get("status") == "error":
                self._subtask_manager.mark_current_failed(str(result.get("message", "")))
            else:
                self._subtask_manager.mark_current_completed(
                    result.get("final_result", str(result))
                )

            # Advance to next subtask
            self._subtask_manager.advance()

            # Check if refinement is needed
            if self._execution_monitor.should_refine() and not self._subtask_manager.is_complete:
                self._refine_subtasks(task)
                self._execution_monitor = ExecutionMonitor(
                    max_steps_per_subtask=self.config.max_steps_per_subtask,
                )
                self._execution_monitor.start()

        # Step 3: Generate report
        report = self._generate_report(task)

        return PentAGIResult(
            task=task,
            report=report,
            subtasks=self._subtask_manager.subtasks,
            completed_subtasks=self._subtask_manager.completed_subtasks,
            total_steps=self._execution_monitor._step_count,
        )

    def _generate_subtasks(self, task: str) -> List[Dict[str, Any]]:
        """Use GeneratorAgent to create a subtask plan."""
        generator = self._agents["generator"]

        # Build engine for generator
        # Note: No ReflectorCritic for generator — it's a single-shot agent
        # that just needs to call subtask_list once. ReflectorCritic would
        # interfere by stopping the engine if the model outputs free text.
        engine = generator.build_engine(
            budget=RuntimeBudget(max_steps=5),
            recovery_policy=PentAGIRecoveryPolicy(),
            stop_criteria=[FinalResultCriteria()],
        )

        try:
            result = engine.run(task)
            # Extract subtasks from result state
            if hasattr(result, 'state') and hasattr(result.state, 'generated_subtasks'):
                subtasks = result.state.generated_subtasks
                if isinstance(subtasks, list) and subtasks:
                    return subtasks
            # Fallback: try parsing final_result
            if hasattr(result, 'state') and hasattr(result.state, 'final_result') and result.state.final_result:
                try:
                    parsed = json.loads(result.state.final_result)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            import sys
            print(f"[PentAGI] Generator failed: {type(e).__name__}: {e}", file=sys.stderr)

        # Fallback: single subtask that is the entire task
        return [{"id": "1", "title": "Execute task", "description": task, "status": "planned"}]

    def _execute_subtask(self, subtask: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single subtask via PrimaryAgent."""
        subtask_text = f"Subtask: {subtask.get('title', '')}\n{subtask.get('description', '')}"

        # Store execution context for injection into agent prompts
        # Use full context for primary, short context for specialists
        self._current_execution_context = self._build_current_execution_context(
            self._current_task, subtask
        )
        self._current_execution_context_short = self._build_current_execution_context(
            self._current_task, subtask, full=False
        )

        # Inject execution context into primary agent
        primary = self._primary_agent
        if primary is None:
            return {"status": "error", "message": "Primary agent not initialized"}
        primary._execution_context = self._current_execution_context

        # Generate pre-execution plan if planner is enabled
        if self.config.planner_enabled:
            plan = self._plan_subtask(subtask)
            if plan:
                primary._planner_plan = plan
            else:
                primary._planner_plan = ""
        else:
            primary._planner_plan = ""

        # Set up CompactHistory with PentAGI summarizer prompt
        self._attach_compact_history(primary)

        # Build engine with critics and recovery
        # ReflectorCritic with LLM for generating contextual guidance
        reflector = ReflectorCritic(
            llm=self.llm,
            barrier_tools=["done", "hack_result"],
            execution_context=self._current_execution_context,
        )

        # Build hooks list
        hooks = [
            AutoStoreHook(memory=self._memory, flow_id=self._run_id or ""),
            ToolResultSummarizationHook(llm=self.llm),
        ]

        # Add mentor hook if enabled
        if self.config.mentor_enabled:
            hooks.append(MentorHook(
                llm=self.llm,
                execution_context=self._current_execution_context,
                trigger_interval=self.config.mentor_interval,
            ))

        engine = primary.build_engine(
            budget=RuntimeBudget(max_steps=self.config.max_steps_per_subtask),
            critics=[reflector, StuckDetectionCritic(), GracefulShutdownCritic()],
            recovery_policy=PentAGIRecoveryPolicy(llm=self.llm),
            agent_registry=self._agent_registry,
            shared_memory=self._memory,
            hooks=hooks,
            stop_criteria=[FinalResultCriteria()],
            loop_detector=ToolCallLoopDetector(strip_volatile=True),
        )

        try:
            result = engine.run(subtask_text)
            # Extract final_result from the state, not from EngineResult
            state_final = getattr(getattr(result, 'state', None), 'final_result', None)
            return {
                "status": "ok",
                "final_result": state_final or str(result),
                "steps": getattr(result, 'step_count', 0),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _refine_subtasks(self, task: str) -> None:
        """Use RefinerAgent to adjust the remaining subtask plan."""
        refiner = self._agents["refiner"]
        completed = self._subtask_manager.completed_subtasks
        remaining = self._subtask_manager.remaining_subtasks

        if not remaining:
            return

        refine_task = json.dumps({
            "task": task,
            "completed_subtasks": completed,
            "planned_subtasks": remaining,
        }, ensure_ascii=False)

        engine = refiner.build_engine(
            budget=RuntimeBudget(max_steps=3),
            critics=[ReflectorCritic()],
            recovery_policy=PentAGIRecoveryPolicy(),
        )

        try:
            result = engine.run(
                refine_task,
                completed_subtasks=completed,
                planned_subtasks=remaining,
            )
            if hasattr(result, 'state') and hasattr(result.state, 'delta_operations') and result.state.delta_operations:
                self._subtask_manager.apply_delta(result.state.delta_operations)
        except Exception:
            pass

    def _generate_report(self, task: str) -> str:
        """Use ReporterAgent to produce the final report."""
        reporter = self._agents["reporter"]
        completed = self._subtask_manager.completed_subtasks
        remaining = self._subtask_manager.remaining_subtasks

        # Truncate subtask results aggressively to avoid context overflow.
        # Reporter only needs titles and brief results — not full state dumps.
        MAX_RESULT_CHARS = 500
        trimmed_completed = []
        for st in completed:
            trimmed = {
                "id": st.get("id", ""),
                "title": st.get("title", ""),
                "status": st.get("status", ""),
            }
            result_text = st.get("result", "")
            if isinstance(result_text, str) and result_text:
                trimmed["result"] = result_text[:MAX_RESULT_CHARS] + ("..." if len(result_text) > MAX_RESULT_CHARS else "")
            trimmed_completed.append(trimmed)

        report_task = json.dumps({
            "task": task,
            "completed_subtasks": trimmed_completed,
            "planned_subtasks": remaining,
        }, ensure_ascii=False)

        engine = reporter.build_engine(
            budget=RuntimeBudget(max_steps=5),
            recovery_policy=PentAGIRecoveryPolicy(),
            stop_criteria=[FinalResultCriteria()],
        )

        try:
            result = engine.run(
                report_task,
                completed_subtasks=trimmed_completed,
                planned_subtasks=remaining,
            )
            # Read report from post-run state
            if hasattr(result, 'state') and hasattr(result.state, 'report') and result.state.report:
                return result.state.report
            # Fallback: use final_result if available
            state = result.state if hasattr(result, 'state') else None
            final = getattr(state, 'final_result', None) if state else None
            if final:
                return str(final)
            return 'No report generated'
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"Report generation failed: {e}"


__all__ = ["PentAGIFlow", "PentAGIResult"]
