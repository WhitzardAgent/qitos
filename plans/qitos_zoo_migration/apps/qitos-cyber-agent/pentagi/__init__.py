"""PentAGI — penetration testing multi-agent system built on QitOS.

This module replicates the PentAGI platform's functionality using the
QitOS framework. It provides a hierarchical multi-agent system for
automated penetration testing with 11 specialized agents, 35+ tools,
Docker execution, browser/scraper, Enricher→Adviser pipeline,
LLM-based Reflector and ToolCallFixer, and vector DB memory.

Usage::

    from qitos.examples.pentagi import PentAGIRunner, PentAGIConfig

    config = PentAGIConfig(
        model_provider="openai-compatible",
        model_name="qwen-plus",
        api_key="your-api-key",
        base_url="https://api.example.com/v1",
        docker_profile="kali",
        authorized_targets=["192.168.1.0/24"],
    )

    runner = PentAGIRunner(config)
    result = runner.run("Penetration test against target.example.com")
    print(result.report)
"""

from .config import PentAGIConfig, get_docker_config
from .runner import PentAGIRunner
from .orchestrator import PentAGIFlow, PentAGIResult, SubtaskManager, ExecutionMonitor
from .memory import PentAGIMemory
from .agents import (
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
from .critic import ReflectorCritic, ToolCallFixerRecovery, StuckDetectionCritic, PentAGIRecoveryPolicy
from .tools import (
    BarrierDone,
    BarrierAsk,
    HackResultTool,
    CodeResultTool,
    MaintenanceResultTool,
    SearchResultTool,
    MemoristResultTool,
    EnricherResultTool,
    SubtaskListTool,
    SubtaskPatchTool,
    ReportResultTool,
    TerminalTool,
    ReadFileTool,
    WriteFileTool,
    ListFilesTool,
    GoogleSearchTool,
    DuckDuckGoSearchTool,
    TavilySearchTool,
    SearXNGSearchTool,
    SploitusSearchTool,
    TraversaalSearchTool,
    PerplexitySearchTool,
    SearchInMemoryTool,
    SearchGuideTool,
    SearchAnswerTool,
    SearchCodeTool,
    StoreGuideTool,
    StoreAnswerTool,
    StoreCodeTool,
    StoreFindingTool,
    BrowserTool,
    AdviceTool,
    build_pentagi_delegate_tools,
    build_delegate_tools_for_agent,
)

__all__ = [
    "PentAGIConfig",
    "get_docker_config",
    "PentAGIRunner",
    "PentAGIFlow",
    "PentAGIResult",
    "SubtaskManager",
    "ExecutionMonitor",
    "PentAGIMemory",
    "PrimaryPentestAgent",
    "PentesterAgent",
    "CoderAgent",
    "InstallerAgent",
    "SearcherAgent",
    "MemoristAgent",
    "GeneratorAgent",
    "RefinerAgent",
    "ReporterAgent",
    "AdviserAgent",
    "EnricherAgent",
    "ReflectorCritic",
    "ToolCallFixerRecovery",
    "StuckDetectionCritic",
    "PentAGIRecoveryPolicy",
    "BarrierDone",
    "BarrierAsk",
    "HackResultTool",
    "CodeResultTool",
    "MaintenanceResultTool",
    "SearchResultTool",
    "MemoristResultTool",
    "EnricherResultTool",
    "SubtaskListTool",
    "SubtaskPatchTool",
    "ReportResultTool",
    "TerminalTool",
    "ReadFileTool",
    "WriteFileTool",
    "ListFilesTool",
    "DuckDuckGoSearchTool",
    "GoogleSearchTool",
    "TavilySearchTool",
    "SearXNGSearchTool",
    "SploitusSearchTool",
    "TraversaalSearchTool",
    "PerplexitySearchTool",
    "SearchInMemoryTool",
    "SearchGuideTool",
    "SearchAnswerTool",
    "SearchCodeTool",
    "StoreGuideTool",
    "StoreAnswerTool",
    "StoreCodeTool",
    "StoreFindingTool",
    "BrowserTool",
    "AdviceTool",
    "build_pentagi_delegate_tools",
    "build_delegate_tools_for_agent",
]
