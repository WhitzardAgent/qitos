"""CLI entry point for running the CyberGym PoC Generation Agent."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_MAX_TOKENS = 8192
GLM_DEFAULT_MAX_TOKENS = 20000
DEFAULT_API_TIMEOUT = 360  # standard benchmark timeout: GLM responses can be long (max_tokens 20000); a short timeout cuts legit generations


def infer_family_id(model: str) -> str | None:
    """Map GLM model names to QitOS' dedicated GLM harness family."""
    normalized_model = model.strip().lower()
    if normalized_model.startswith("glm-") or normalized_model.startswith("zai-org/glm-") or normalized_model.startswith("glm5"):
        return "glm"
    return None


def default_max_tokens_for_model(model: str) -> int:
    """Return the CyberGym default output budget for a model name."""
    if infer_family_id(model) == "glm":
        return GLM_DEFAULT_MAX_TOKENS
    return DEFAULT_MAX_TOKENS


def build_agent(
    *,
    model: str = "claude-sonnet-4-6",
    workspace_root: str = ".",
    task_root: Optional[str] = None,
    server_url: str = "http://localhost:8000",
    memory_dir: Optional[str] = None,
    global_memory_dir: Optional[str] = None,
    max_steps: int = 30,
    shell_timeout: int = 60,
    llm_config: Optional[Dict[str, Any]] = None,
    agent_mode: Optional[str] = None,
) -> "CyberGymAgent":
    """Build and configure a CyberGymAgent instance."""
    from .agent import CyberGymAgent

    llm = _create_llm(model, llm_config=llm_config)

    agent = CyberGymAgent(
        llm=llm,
        workspace_root=workspace_root,
        task_root=task_root,
        server_url=server_url,
        memory_dir=memory_dir,
        global_memory_dir=global_memory_dir,
        max_steps=max_steps,
        shell_timeout=shell_timeout,
        agent_mode=agent_mode,
    )

    return agent


def _create_llm(model: str, llm_config: Optional[Dict[str, Any]] = None):
    """Create an LLM provider using the QitOS harness preset system.

    The harness preset stamps the model with qitos_harness_metadata that
    the Engine reads to auto-configure native tool calling, protocol,
    and parser. No manual parser/protocol passing is needed.
    """
    from qitos.harness import build_model_for_preset

    llm_config = llm_config or {}
    api_key = llm_config.get("api_key") or os.getenv("OPENAI_API_KEY", "")
    base_url = llm_config.get("base_url") or os.getenv("OPENAI_BASE_URL", "")
    family_id = infer_family_id(model)

    if not api_key or not base_url:
        raise RuntimeError(
            f"LLM provider requires api_key and base_url. "
            "Pass --api-key and --base-url, or set OPENAI_API_KEY and OPENAI_BASE_URL."
        )

    llm = build_model_for_preset(
        model_name=model,
        family_id=family_id,
        api_key=api_key,
        base_url=base_url,
        temperature=llm_config.get("temperature", 0.7),
        max_tokens=llm_config.get("max_tokens", default_max_tokens_for_model(model)),
        timeout=llm_config.get("timeout", DEFAULT_API_TIMEOUT),
    )

    return llm


def run_task(
    task_dir: str,
    *,
    model: str = "claude-sonnet-4-6",
    server_url: str = "http://localhost:8000",
    workspace_root: Optional[str] = None,
    max_steps: int = 30,
    task_id: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    agent_mode: Optional[str] = None,
) -> None:
    """Run the CyberGym agent on a single task."""
    task_path = Path(task_dir).resolve()

    llm_config: Dict[str, Any] = {}
    if api_key:
        llm_config["api_key"] = api_key
    if base_url:
        llm_config["base_url"] = base_url

    # Create a QitOS Task from the CyberGym task directory
    from .adapter import CyberGymAdapter
    adapter = CyberGymAdapter(server_url=server_url)
    task = adapter.from_task_dir(str(task_path), task_id=task_id, max_steps=max_steps)
    ws = workspace_root or task.inputs.get("task_root") or str(task_path)

    # Build the agent (Engine auto-detects parser/protocol from harness metadata)
    agent = build_agent(
        model=model,
        workspace_root=ws,
        task_root=str(task_path),
        server_url=server_url,
        max_steps=max_steps,
        llm_config=llm_config or None,
        agent_mode=agent_mode,
    )

    # Set up environment
    from .env import CyberGymEnv
    env = CyberGymEnv(
        workspace_root="/workspace",
        image="ubuntu:22.04",
        host_workspace=ws,
        auto_create=True,
        remove_on_close=True,
    )

    # Stop criteria
    from .stop_criteria import PoCVerificationCriteria
    from qitos.engine.stop_criteria import FinalResultCriteria, MaxStepsCriteria
    stop_criteria = [
        PoCVerificationCriteria(),
        FinalResultCriteria(),
        MaxStepsCriteria(max_steps=max_steps),
    ]

    # Context config for overflow protection
    from qitos.engine.states import ContextConfig
    context_config = ContextConfig(
        tool_result_max_chars=60000,
        conversation_max_rounds=0,
        loop_max_repeats=3,
    )

    print(f"[CyberGym] Starting agent for task: {task.id}")
    print(f"[CyberGym] Agent ID: {adapter.agent_id}")
    print(f"[CyberGym] Workspace: {ws}")
    print(f"[CyberGym] Server: {server_url}")
    print(f"[CyberGym] Model: {model}")

    # Run the agent -- Engine auto-detects protocol/parser from llm harness metadata
    result = agent.run(
        task=task,
        env=env,
        stop_criteria=stop_criteria,
        max_steps=max_steps,
        workspace=ws,
        context_config=context_config,
        # Pass CyberGym metadata through state_kwargs
        description=task.inputs.get("description", ""),
        task_id=task.inputs.get("task_id", ""),
        agent_id=task.inputs.get("agent_id", ""),
        checksum=task.inputs.get("checksum", ""),
        server_url=task.inputs.get("server_url", server_url),
        task_root=task.inputs.get("task_root", str(task_path)),
        source_root=task.inputs.get("source_root", ws),
        repo_dir=task.inputs.get("source_root", task.inputs.get("repo_dir", "")),
    )

    print(f"\n[CyberGym] Agent finished.")
    print(f"[CyberGym] Result: {result}")

    return result


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CyberGym PoC Generation Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m cybergym_agent run /path/to/task_dir --server http://localhost:8000\n"
            "  python -m cybergym_agent run /path/to/task_dir --model qwen3-coder-next \\\n"
            "      --api-key sk-xxx --base-url https://dashscope.aliyuncs.com/compatible-mode/v1\n"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run agent on a CyberGym task")
    run_parser.add_argument(
        "task_dir", type=str, help="Path to the CyberGym task directory",
    )
    run_parser.add_argument(
        "--server", type=str, default="http://localhost:8000",
        help="CyberGym verification server URL (default: http://localhost:8000)",
    )
    run_parser.add_argument(
        "--model", type=str, default="claude-sonnet-4-6",
        help="LLM model identifier (default: claude-sonnet-4-6)",
    )
    run_parser.add_argument(
        "--max-steps", type=int, default=30,
        help="Maximum agent steps (default: 30)",
    )
    run_parser.add_argument(
        "--task-id", type=str, default=None,
        help="Override task ID (auto-detected if not provided)",
    )
    run_parser.add_argument(
        "--workspace", type=str, default=None,
        help="Override workspace root (defaults to task_dir)",
    )
    run_parser.add_argument(
        "--api-key", type=str, default=None,
        help="API key for the LLM provider",
    )
    run_parser.add_argument(
        "--base-url", type=str, default=None,
        help="Base URL for the LLM provider API",
    )
    run_parser.add_argument(
        "--agent-mode", type=str, default=None,
        help=(
            "Agent runtime mode. If omitted, uses CYBERGYM_AGENT_MODE; "
            "if that is unset, uses classic."
        ),
    )

    args = parser.parse_args()

    if args.command == "run":
        run_task(
            task_dir=args.task_dir,
            model=args.model,
            server_url=args.server,
            workspace_root=args.workspace,
            max_steps=args.max_steps,
            task_id=args.task_id,
            api_key=args.api_key,
            base_url=args.base_url,
            agent_mode=args.agent_mode,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
