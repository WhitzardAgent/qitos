"""CLI entry point for the Claude Code agent.

Uses the built-in QitOS AgentREPL for a clean conversational experience
that mirrors Claude Code's UX: streaming output, permission confirmation,
markdown rendering, slash commands, and multi-turn conversation.
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

from qitos.kit.repl import AgentREPL


def main(argv: Optional[list] = None) -> None:
    """Run the Claude Code agent from the command line."""
    parser = argparse.ArgumentParser(
        prog="claude-code",
        description="Claude Code — AI coding assistant built on QitOS",
    )
    parser.add_argument("--workspace", "-w", default=".", help="Workspace root directory")
    parser.add_argument("--model", "-m", default=None, help="Model name")
    parser.add_argument("--base-url", default=None, help="API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument(
        "--protocol",
        choices=[
            "react_text_v1", "json_decision_v1", "xml_decision_v1",
            "tool_use_xml_v1", "terminus_json_v1", "terminus_xml_v1",
            "minimax_tool_call_v1", "kimi_tool_call_v1",
        ],
        default=None,
        help="Prompt protocol (default: auto-detect)",
    )
    parser.add_argument(
        "--permission-mode",
        choices=["default", "plan", "acceptEdits", "bypassPermissions", "auto"],
        default="default",
    )
    parser.add_argument("--max-steps", type=int, default=50, help="Max agent steps")
    parser.add_argument("--task", "-t", default=None, help="Task (non-interactive)")
    parser.add_argument("--repl", action="store_true", help="Interactive REPL")

    args = parser.parse_args(argv)

    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        print(f"Error: workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", "")

    llm, protocol = _build_model(args.model, api_key, base_url)
    if args.protocol:
        protocol = args.protocol
    from qitos.protocols import parser_from_protocol
    model_parser = parser_from_protocol(protocol) if protocol else None

    from .agent import ClaudeCodeAgent

    agent = ClaudeCodeAgent(
        llm=llm,
        workspace_root=workspace,
        max_steps=args.max_steps,
        permission_mode=args.permission_mode,
        model_parser=model_parser,
        model_protocol=protocol,
    )

    repl = AgentREPL(agent=agent, workspace=workspace, max_steps=args.max_steps)

    if args.repl or (not args.task):
        repl.run()
    else:
        repl.run_headless(args.task)


# ---------------------------------------------------------------------------
# Model / protocol helpers
# ---------------------------------------------------------------------------

def _build_model(model_name, api_key, base_url):
    from qitos.models import OpenAICompatibleModel
    from qitos.models.profile_registry import infer_default_protocol

    if not model_name:
        model_name = os.environ.get("OPENAI_MODEL", "")
    if not model_name:
        print("Error: No model specified. Use --model.", file=sys.stderr)
        sys.exit(1)
    if not api_key:
        print("Error: No API key. Set OPENAI_API_KEY or use --api-key.", file=sys.stderr)
        sys.exit(1)

    protocol = infer_default_protocol(model_name) or "json_decision_v1"
    llm = OpenAICompatibleModel(model=model_name, api_key=api_key, base_url=base_url or None)
    return llm, protocol


if __name__ == "__main__":
    main()
