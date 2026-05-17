"""AdviceTool — Enricher→Adviser pipeline for strategic guidance.

Replicates pentagi's advice handler which:
1. Runs Enricher (multi-turn agent loop) to gather context from
   memory, browser, terminal, and filesystem
2. Passes enrichment data to Adviser (single LLM call) for guidance
3. Returns the adviser's advice as the tool result

This tool replaces the simple "advice" delegate tool with a pipeline
that provides enriched, context-aware guidance.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from qitos.core.agent_spec import AgentRegistry
from qitos.core.tool import BaseTool, ToolSpec
from qitos.engine.states import RuntimeBudget

from ..agents.enricher import EnricherAgent
from ..agents.adviser import AdviserAgent
from ..critic import ReflectorCritic
from ..critic.pentagi_recovery import PentAGIRecoveryPolicy
from ..memory.pentagi_memory import PentAGIMemory
from ..prompts.question_adviser_prompt import QUESTION_ADVISER_PROMPT
from ..prompts.question_enricher_prompt import QUESTION_ENRICHER_PROMPT


class AdviceTool(BaseTool):
    """Get strategic advice from the Enricher→Adviser pipeline.

    When called, this tool:
    1. Runs the Enricher agent to gather supplementary context
       from memory, browser, terminal, and filesystem
    2. Passes the enrichment data to the Adviser agent
    3. Returns the adviser's guidance

    Parameters
    ----------
    llm : Any
        LLM instance for both Enricher and Adviser.
    memory : PentAGIMemory
        Shared memory instance for context retrieval.
    agent_registry : AgentRegistry | None
        Agent registry for delegate tools inside Enricher.
    docker_image : str
        Docker image for environment context.
    language : str
        Response language.
    """

    def __init__(
        self,
        llm: Any,
        memory: PentAGIMemory,
        agent_registry: Optional[AgentRegistry] = None,
        docker_image: str = "kalilinux/kali-rolling",
        language: str = "en",
    ):
        self._llm = llm
        self._memory = memory
        self._agent_registry = agent_registry
        self._docker_image = docker_image
        self._language = language
        super().__init__(
            ToolSpec(
                name="advice",
                description=(
                    "Get strategic advice and guidance from the technical solution "
                    "optimization expert. Use when encountering challenges, needing "
                    "strategic direction, or requiring expert guidance on approach. "
                    "Provide your question, relevant code snippets, and command output."
                ),
                parameters={
                    "question": {
                        "type": "string",
                        "description": "Your question or the challenge you're facing",
                    },
                    "code": {
                        "type": "string",
                        "description": "Relevant code snippet if your request relates to code",
                    },
                    "output": {
                        "type": "string",
                        "description": "Relevant command output if your request relates to terminal issues",
                    },
                    "message": {
                        "type": "string",
                        "description": "Task result message",
                    },
                },
                required=["question", "message"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        question = str(args.get("question", ""))
        code = str(args.get("code", ""))
        output = str(args.get("output", ""))
        message = str(args.get("message", ""))

        if not question:
            return {"status": "error", "message": "Question is required"}

        # Determine initiator agent from runtime context
        initiator_agent = "unknown"
        if runtime_context and "agent_name" in runtime_context:
            initiator_agent = runtime_context["agent_name"]

        # Step 1: Run Enricher to gather supplementary context
        enrichment_data = self._run_enricher(question, code, output)

        # Step 2: Run Adviser with enriched context
        advice = self._run_adviser(question, code, output, enrichment_data, initiator_agent)

        return {
            "status": "ok",
            "type": "advice",
            "advice": advice,
            "enrichment_data": enrichment_data[:500] if enrichment_data else "",
            "message": message,
        }

    def _run_enricher(self, question: str, code: str, output: str) -> str:
        """Run the Enricher agent to gather supplementary context.

        The Enricher has a multi-turn agent loop with tools
        (search_in_memory, terminal, file, browser) to find
        supplementary information.
        """
        try:
            enricher = EnricherAgent(
                llm=self._llm,
                language=self._language,
            )

            # Build question prompt for enricher
            code_section = f"  <code_snippet>\n  {code}\n  </code_snippet>" if code else ""
            output_section = f"  <command_output>\n  {output}\n  </command_output>" if output else ""

            enricher_task = QUESTION_ENRICHER_PROMPT.format(
                question=question,
                code_section=code_section,
                output_section=output_section,
            )

            # Build tools for enricher
            from ..tools.barrier import EnricherResultTool, TerminalTool, ReadFileTool, WriteFileTool, ListFilesTool
            from ..tools.search_network import SearchInMemoryTool
            from ..tools.browser import BrowserTool

            tools = [
                EnricherResultTool(),
                TerminalTool(), ReadFileTool(), WriteFileTool(), ListFilesTool(),
                SearchInMemoryTool(memory=self._memory),
            ]

            # Build engine and run
            state = enricher.init_state(enricher_task, max_steps=5)
            engine = enricher.build_engine(
                budget=RuntimeBudget(max_steps=5),
                critics=[ReflectorCritic(
                    llm=self._llm,
                    barrier_tools=["enricher_result"],
                )],
                recovery_policy=PentAGIRecoveryPolicy(llm=self._llm),
            )

            result = engine.run(enricher_task)

            # Extract enrichment result
            if hasattr(result, 'state') and hasattr(result.state, 'scratchpad'):
                return "\n".join(result.state.scratchpad[-3:])
            if hasattr(result, 'final_result'):
                return str(result.final_result)

        except Exception as e:
            # Enricher failure is non-fatal — continue without enrichment
            pass

        return ""

    def _run_adviser(
        self,
        question: str,
        code: str,
        output: str,
        enrichment_data: str,
        initiator_agent: str,
    ) -> str:
        """Run the Adviser agent with enriched context.

        The Adviser makes a single LLM call (no tools) to synthesize
        advice from the question + enrichment data.
        """
        try:
            adviser = AdviserAgent(
                llm=self._llm,
                docker_image=self._docker_image,
                language=self._language,
            )

            # Build question prompt for adviser
            enrichment_section = ""
            if enrichment_data:
                enrichment_section = f"  <enrichment_data>\n  {enrichment_data}\n  </enrichment_data>"

            code_section = f"  <code_snippet>\n  {code}\n  </code_snippet>" if code else ""
            output_section = f"  <command_output>\n  {output}\n  </command_output>" if output else ""

            adviser_task = QUESTION_ADVISER_PROMPT.format(
                initiator_agent=initiator_agent,
                enrichment_section=enrichment_section,
                question=question,
                code_section=code_section,
                output_section=output_section,
            )

            # Build engine and run (single-shot, no tools)
            state = adviser.init_state(adviser_task, max_steps=2)
            engine = adviser.build_engine(
                budget=RuntimeBudget(max_steps=2),
                critics=[],
                recovery_policy=PentAGIRecoveryPolicy(llm=self._llm),
            )

            result = engine.run(adviser_task)

            if hasattr(result, 'final_result') and result.final_result:
                return str(result.final_result)

        except Exception as e:
            return f"Adviser error: {e}"

        return "No advice available"


__all__ = ["AdviceTool"]
