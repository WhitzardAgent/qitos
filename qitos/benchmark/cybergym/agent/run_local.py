"""Run the CyberGym agent locally (code audit + PoC generation, no Docker verification).

Usage:
    python -m cybergym_agent.run_local --task-id arvo:3938 --data-dir /path/to/repos/data
    python -m cybergym_agent.run_local --task-dir /path/to/prepared/task_dir

This script:
1. Prepares the task directory using CyberGym's task generation (or uses an existing one)
2. Extracts repo-vul.tar.gz so the agent can read source code
3. Runs the agent with HostEnv (local filesystem, no Docker)
4. The agent does code audit and writes a PoC file -- but does NOT submit to server
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def run_local(
    *,
    task_id: Optional[str] = None,
    data_dir: Optional[str] = None,
    task_dir: Optional[str] = None,
    difficulty: str = "level1",
    model: str = "qwen3-coder-next",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    server_url: str = "http://localhost:8000",
    max_steps: int = 30,
    exploration_only: bool = False,
) -> Any:
    """Run the CyberGym agent locally without Docker."""

    # ------------------------------------------------------------------
    # 1. Prepare the task directory and get a QitOS Task
    # ------------------------------------------------------------------
    from cybergym_agent.adapter import CyberGymAdapter

    adapter = CyberGymAdapter(server_url=server_url)

    if task_dir:
        qitos_task = adapter.from_task_dir(task_dir, task_id=task_id, difficulty=difficulty, max_steps=max_steps)
        # Workspace = task root (e.g. arvo_17986/) so the agent can access
        # description.txt, README.md, repo-vul/, etc.  Using source_root
        # (e.g. repo-vul/graphicsmagick/) hides those Level-1 resources.
        workspace = str(qitos_task.inputs.get("task_root") or Path(task_dir).resolve())
    elif task_id and data_dir:
        qitos_task = adapter.from_data_dir(
            task_id=task_id,
            data_dir=data_dir,
            difficulty=difficulty,
            max_steps=max_steps,
        )
        workspace = str(qitos_task.inputs.get("task_root") or qitos_task.inputs["repo_dir"])
    else:
        raise ValueError("Provide --task-dir or both --task-id and --data-dir")

    # ------------------------------------------------------------------
    # 2. Create the LLM (Engine auto-detects protocol/parser from harness metadata)
    # ------------------------------------------------------------------
    from cybergym_agent.cli import _create_llm

    llm_config: Dict[str, Any] = {}
    if api_key:
        llm_config["api_key"] = api_key
    if base_url:
        llm_config["base_url"] = base_url

    llm = _create_llm(model, llm_config=llm_config)

    # ------------------------------------------------------------------
    # 3. Build the agent
    # ------------------------------------------------------------------
    from cybergym_agent.agent import CyberGymAgent

    agent = CyberGymAgent(
        llm=llm,
        workspace_root=workspace,
        task_root=str(qitos_task.inputs.get("task_root") or Path(task_dir).resolve() if task_dir else qitos_task.inputs.get("task_root") or workspace),
        server_url=server_url,
        max_steps=max_steps,
    )

    # ------------------------------------------------------------------
    # 4. Run with HostEnv (local filesystem, no Docker)
    # ------------------------------------------------------------------
    from qitos.kit.env.host_env import HostEnv
    from qitos.engine.stop_criteria import FinalResultCriteria, MaxStepsCriteria
    from cybergym_agent.stop_criteria import PoCVerificationCriteria, PhaseExitCriteria
    from qitos.engine.states import ContextConfig

    env = HostEnv(workspace_root=workspace)

    stop_criteria = [
        PoCVerificationCriteria(),
        FinalResultCriteria(),
        MaxStepsCriteria(max_steps=max_steps),
    ]

    if exploration_only:
        effective_max_steps = min(max_steps, 15)
        stop_criteria = [
            PhaseExitCriteria(phase="exploration"),
            MaxStepsCriteria(max_steps=effective_max_steps),
        ]
        server_url = "http://localhost:0"  # dummy, no submit needed

    context_config = ContextConfig(
        tool_result_max_chars=60000,
        conversation_max_rounds=0,
        loop_max_repeats=3,
    )

    print(f"[CyberGym Local] Task: {qitos_task.id}")
    print(f"[CyberGym Local] Agent ID: {adapter.agent_id}")
    print(f"[CyberGym Local] Workspace: {workspace}")
    print(f"[CyberGym Local] Model: {model}")
    print(f"[CyberGym Local] Difficulty: {difficulty}")
    print(f"[CyberGym Local] Max steps: {max_steps}")
    print(f"[CyberGym Local] Repo dir: {qitos_task.inputs.get('repo_dir', 'N/A')}")

    # Engine auto-detects protocol/parser from llm.qitos_harness_metadata
    result = agent.run(
        task=qitos_task,
        env=env,
        stop_criteria=stop_criteria,
        max_steps=max_steps,
        workspace=workspace,
        context_config=context_config,
        **qitos_task.inputs,
    )

    print(f"\n[CyberGym Local] Agent finished.")
    print(f"[CyberGym Local] Result: {result}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Run CyberGym agent locally (code audit + PoC generation, no Docker)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # From raw data directory:\n"
            "  python -m cybergym_agent.run_local --task-id arvo:3938 \\\n"
            "      --data-dir /path/to/repos/data --difficulty level1 \\\n"
            "      --api-key sk-xxx --base-url https://dashscope.aliyuncs.com/compatible-mode/v1\n"
            "\n"
            "  # From a prepared task directory:\n"
            "  python -m cybergym_agent.run_local --task-dir /path/to/task_out \\\n"
            "      --api-key sk-xxx --base-url https://dashscope.aliyuncs.com/compatible-mode/v1\n"
        ),
    )

    # Task source (mutually exclusive group)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--task-id", type=str,
        help="CyberGym task ID (e.g., arvo:3938). Requires --data-dir.",
    )
    group.add_argument(
        "--task-dir", type=str,
        help="Path to an already-prepared CyberGym task directory.",
    )

    # Data directory (required with --task-id)
    parser.add_argument(
        "--data-dir", type=str, default=None,
        help="Path to CyberGym data root (containing arvo/, oss-fuzz/). Required with --task-id.",
    )
    parser.add_argument(
        "--difficulty", type=str, default="level1",
        choices=["level0", "level1", "level2", "level3"],
        help="Task difficulty (default: level1)",
    )

    # LLM config
    parser.add_argument(
        "--model", type=str, default="qwen3-coder-next",
        help="LLM model identifier (default: qwen3-coder-next)",
    )
    parser.add_argument(
        "--api-key", type=str, required=True,
        help="API key for the LLM provider",
    )
    parser.add_argument(
        "--base-url", type=str, required=True,
        help="Base URL for the LLM provider API",
    )

    # General config
    parser.add_argument(
        "--server", type=str, default="http://localhost:8000",
        help="CyberGym verification server URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--max-steps", type=int, default=30,
        help="Maximum agent steps (default: 30)",
    )
    parser.add_argument(
        "--exploration-only", action="store_true", default=False,
        help="Stop after exploration phase (for sink recall evaluation)",
    )

    args = parser.parse_args()

    if args.task_id and not args.data_dir:
        parser.error("--task-id requires --data-dir")

    run_local(
        task_id=args.task_id,
        data_dir=args.data_dir,
        task_dir=args.task_dir,
        difficulty=args.difficulty,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        server_url=args.server,
        max_steps=args.max_steps,
        exploration_only=args.exploration_only,
    )


if __name__ == "__main__":
    main()
