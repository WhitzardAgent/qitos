"""Top-level qit CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from qitos.benchmark import (
    build_experiment_spec,
    evaluate_benchmark_results,
    load_benchmark_tasks,
    normalize_benchmark_name,
    read_benchmark_results,
    resolve_builtin_runner,
    resolve_runner,
    run_benchmark_tasks,
    write_benchmark_results,
)
from qitos.demo.minimal import main as minimal_demo_main
from qitos.core.spec import RunSpec
from qitos.kit.skill.cli import main as skill_main
from qitos.qita._cli_app import _cmd_export as qita_export
from qitos.qita._cli_app import _cmd_replay as qita_replay


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        parser = argparse.ArgumentParser(
            prog="qit", description="QitOS CLI for demos, benchmarks, and developer workflows"
        )
        subparsers = parser.add_subparsers(dest="command")
        subparsers.add_parser("demo", help="Run packaged demos and quickstarts")
        subparsers.add_parser("skill", help="Manage third-party skills")
        subparsers.add_parser("bench", help="Unified benchmark CLI")
        subparsers.add_parser("experiment", help="Run parameter-sweep experiments")
        subparsers.add_parser("new", help="Scaffold a new agent from a template")
        subparsers.add_parser("list-templates", help="List built-in agent templates")
        parser.print_help()
        return 0
    if args:
        command = args[0]
        remaining = args[1:]
        if command == "demo":
            return _demo_main(remaining)
        if command == "skill":
            return skill_main(remaining)
        if command == "bench":
            return _bench_main(remaining)
        if command == "experiment":
            return _experiment_main(remaining)
        if command == "new":
            return _new_main(remaining)
        if command == "list-templates":
            return _list_templates_main(remaining)
    parser = argparse.ArgumentParser(
        prog="qit", description="QitOS CLI for demos, benchmarks, and developer workflows"
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("demo", help="Run packaged demos and quickstarts")
    subparsers.add_parser("skill", help="Manage third-party skills")
    subparsers.add_parser("bench", help="Unified benchmark CLI")
    subparsers.add_parser("experiment", help="Run parameter-sweep experiments")
    subparsers.add_parser("new", help="Scaffold a new agent from a template")
    subparsers.add_parser("list-templates", help="List built-in agent templates")
    parser.print_help()
    return 1


def _demo_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="qit demo", description="QitOS packaged demos")
    sub = parser.add_subparsers(dest="command", required=True)

    p_minimal = sub.add_parser(
        "minimal",
        help="Run the minimal coding agent demo and write a qita-ready trace",
    )
    p_minimal.add_argument(
        "--workspace",
        default="./playground/minimal_coding_agent",
        help="Workspace directory used by the coding demo",
    )
    p_minimal.add_argument(
        "--logdir",
        default="./runs",
        help="Trace log directory discovered by qita",
    )
    p_minimal.add_argument(
        "--render",
        action="store_true",
        help="Enable terminal rendering while the demo runs",
    )
    p_minimal.add_argument("--model-name", help="Override the model name")
    p_minimal.add_argument("--base-url", help="Override the OpenAI-compatible base URL")
    p_minimal.add_argument("--api-key", help="Override OPENAI_API_KEY / QITOS_API_KEY")
    p_minimal.add_argument("--task", default=None, help="Override the coding task text")
    p_minimal.add_argument(
        "--max-steps",
        type=int,
        default=8,
        help="Maximum step budget for the coding loop",
    )

    args = parser.parse_args(argv)
    if args.command == "minimal":
        return minimal_demo_main(
            workspace=args.workspace,
            trace_logdir=args.logdir,
            render=bool(args.render),
            api_key=args.api_key,
            model_name=args.model_name,
            base_url=args.base_url,
            task=str(args.task) if args.task else "",
            max_steps=int(args.max_steps),
        )
    return 1


def _bench_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="qit bench", description="QitOS benchmark CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Prepare or run benchmark tasks")
    p_run.add_argument("--benchmark", required=True)
    p_run.add_argument("--split", required=True)
    p_run.add_argument("--subset")
    p_run.add_argument("--limit", type=int)
    p_run.add_argument("--root")
    p_run.add_argument("--strategy", default="dry_run")
    p_run.add_argument("--runner")
    p_run.add_argument("--output", required=True)
    p_run.add_argument("--model-name")
    p_run.add_argument("--model-family")
    p_run.add_argument("--prompt-protocol", default="react_text_v1")
    p_run.add_argument("--parser-name", default="ReActTextParser")
    p_run.add_argument("--trace-schema-version", default="v1")
    p_run.add_argument("--trace-logdir", default="./runs")
    p_run.add_argument("--seed", type=int)
    p_run.add_argument("--base-url")
    p_run.add_argument("--api-key")

    p_eval = sub.add_parser("eval", help="Aggregate benchmark results")
    p_eval.add_argument("--input", required=True)
    p_eval.add_argument("--json", action="store_true")

    p_replay = sub.add_parser("replay", help="Replay one benchmark run")
    p_replay.add_argument("--run", required=True)
    p_replay.add_argument("--host", default="127.0.0.1")
    p_replay.add_argument("--port", type=int, default=8765)
    p_replay.add_argument("--print-url", action="store_true")

    p_export = sub.add_parser("export", help="Export one benchmark run")
    p_export.add_argument("--run", required=True)
    p_export.add_argument("--html", required=True)

    p_list = sub.add_parser("list", help="List available benchmarks and splits")
    p_list.add_argument("--benchmark", default=None, help="Show splits for a specific benchmark")

    p_presets = sub.add_parser("presets", help="List available model-family presets")

    args = parser.parse_args(argv)
    if args.command == "run":
        return _bench_run(args)
    if args.command == "eval":
        return _bench_eval(args)
    if args.command == "replay":
        return _bench_replay(args)
    if args.command == "export":
        return _bench_export(args)
    if args.command == "list":
        return _bench_list(args)
    if args.command == "presets":
        return _bench_presets(args)
    return 1


def _bench_run(args: argparse.Namespace) -> int:
    benchmark = normalize_benchmark_name(args.benchmark)
    tasks = load_benchmark_tasks(
        benchmark=benchmark,
        split=args.split,
        limit=args.limit,
        subset=args.subset,
        root=args.root,
    )
    run_spec = RunSpec.infer(
        model_name=args.model_name,
        prompt_protocol=args.prompt_protocol,
        parser_name=args.parser_name,
        trace_schema_version=args.trace_schema_version,
        benchmark_name=benchmark,
        benchmark_split=args.split,
        environment={
            "trace_logdir": str(args.trace_logdir),
            "base_url": str(args.base_url or ""),
        },
        seed=args.seed,
        metadata={
            "subset": args.subset,
            "api_key_present": bool(str(args.api_key or "").strip()),
            "benchmark_alias": str(args.benchmark),
        },
    )
    if args.model_family:
        run_spec.model_family = args.model_family
    experiment_spec = build_experiment_spec(
        benchmark=benchmark,
        split=args.split,
        subset=args.subset,
        limit=args.limit,
        run_spec=run_spec,
    )
    runner = resolve_runner(args.runner) or resolve_builtin_runner(
        benchmark=benchmark,
        strategy=str(args.strategy),
    )
    rows = run_benchmark_tasks(
        tasks=tasks,
        benchmark=benchmark,
        split=args.split,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        runner=runner,
        strategy=str(args.strategy),
    )
    target = write_benchmark_results(args.output, rows)
    summary = evaluate_benchmark_results(rows)
    payload = {
        "output": str(target),
        "count": len(rows),
        "run_spec": run_spec.to_dict(),
        "experiment_spec": experiment_spec.to_dict(),
        "summary": summary,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _bench_eval(args: argparse.Namespace) -> int:
    rows = read_benchmark_results(args.input)
    summary = evaluate_benchmark_results(rows)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"benchmark={summary.get('benchmark')} split={summary.get('split')}")
        print(
            f"total={summary.get('total', 0)} success_rate={summary.get('success_rate', 0.0):.3f} avg_steps={summary.get('avg_steps', 0.0):.2f}"
        )
    return 0


def _bench_replay(args: argparse.Namespace) -> int:
    run_dir = Path(args.run).expanduser().resolve()
    if args.print_url:
        print(f"http://{args.host}:{int(args.port)}/replay/{run_dir.name}")
        return 0
    return qita_replay(run=str(run_dir), host=str(args.host), port=int(args.port))


def _bench_export(args: argparse.Namespace) -> int:
    return qita_export(run=str(args.run), html_path=str(args.html))


def _bench_list(args: argparse.Namespace) -> int:
    if args.benchmark:
        benchmark = normalize_benchmark_name(args.benchmark)
        try:
            tasks = load_benchmark_tasks(benchmark=benchmark, split="test")
            print(f"benchmark={benchmark}  test_tasks={len(tasks)}")
        except Exception as exc:
            print(f"Could not load benchmark {benchmark}: {exc}", file=sys.stderr)
            return 1
        return 0

    known_benchmarks = [
        ("gaia", "GAIA — general AI assistant benchmark"),
        ("tau-bench", "Tau-Bench — tool-use agent benchmark (airline/retail)"),
        ("cybench", "CyBench — cybersecurity CTF benchmark"),
        ("cybergym", "CyberGym — cyber risk assessment benchmark"),
        ("desktop-starter", "Desktop Starter — mock desktop environment tasks"),
        ("osworld", "OSWorld — real desktop OS interaction benchmark"),
    ]
    for name, description in known_benchmarks:
        print(f"  {name:20s}  {description}")
    return 0


def _bench_presets(args: argparse.Namespace) -> int:
    from qitos.harness._presets import known_family_presets

    gold_ids = {"qwen", "kimi", "minimax", "gpt-oss", "gemma-4"}
    for preset in known_family_presets():
        marker = " *" if preset.id in gold_ids else ""
        ctx = preset.context_policy.context_window_hint
        ctx_str = f"{ctx // 1000}k" if ctx else "-"
        models = ", ".join(preset.recommended_models[:2]) if preset.recommended_models else "-"
        print(
            f"  {preset.id:12s}{marker}  {preset.display_name:16s}  "
            f"{preset.default_protocol:26s}  {preset.tool_policy.primary_delivery:18s}  "
            f"ctx={ctx_str:>6s}  {models}"
        )
    print()
    print("  * = gold preset (most thoroughly tested)")
    return 0


def _experiment_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="qit experiment", description="QitOS experiment runner"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run an experiment from a YAML config")
    p_run.add_argument("--config", required=True, help="Path to experiment YAML config")
    p_run.add_argument("--output", default="./runs/experiments", help="Output directory")
    p_run.add_argument("--cache", choices=["memory", "disk"], default=None, help="Cache backend")
    p_run.add_argument("--cache-dir", default="./runs/cache", help="Cache directory (disk backend)")
    p_run.add_argument("--concurrency", type=int, default=1, help="Parallel tasks")
    p_run.add_argument("--resume", action="store_true", help="Skip completed tasks")
    p_run.add_argument("--agent-module", default=None, help="Dotted path to AgentModule subclass")

    args = parser.parse_args(argv)
    if args.command == "run":
        return _experiment_run(args)
    return 1


def _experiment_run(args: argparse.Namespace) -> int:
    from qitos.config.loader import load_agent_config
    from qitos.experiment import ExperimentRunner, SweepSpec
    from qitos.experiment.sweep import sweep_product
    from qitos.core.spec import ExperimentSpec

    config = load_agent_config(args.config)

    # Parse sweep from config metadata if present
    sweep = SweepSpec()
    sweep_raw = config.metadata.get("sweep", {})
    if sweep_raw and isinstance(sweep_raw, dict):
        sweep = SweepSpec(params=sweep_raw)

    experiment_spec = ExperimentSpec(
        name=config.name,
        benchmark_name=config.metadata.get("benchmark"),
        benchmark_split=config.metadata.get("split"),
        metadata=config.metadata,
    )

    # Build cache config
    cache_config = None
    if args.cache == "disk":
        cache_config = {"backend": "disk", "dir": args.cache_dir}
    elif args.cache == "memory":
        cache_config = {"backend": "memory"}

    # Build checkpoint config from metadata
    checkpoint_config = None
    cp_raw = config.metadata.get("checkpoint", {})
    if cp_raw and isinstance(cp_raw, dict):
        checkpoint_config = cp_raw

    # Resolve agent module
    agent = None
    if args.agent_module:
        import importlib

        parts = args.agent_module.rsplit(".", 1)
        if len(parts) == 2:
            module = importlib.import_module(parts[0])
            agent_cls = getattr(module, parts[1], None)
            if agent_cls is not None:
                agent = agent_cls()
    if agent is None:
        print(
            "Warning: No --agent-module provided. "
            "ExperimentRunner requires an agent to run tasks.",
            file=sys.stderr,
        )
        return 1

    runner = ExperimentRunner(
        agent=agent,
        config=config,
        sweep=sweep,
        experiment_spec=experiment_spec,
        cache_config=cache_config,
        checkpoint_config=checkpoint_config,
        concurrency=args.concurrency,
        resume=args.resume,
        output_dir=args.output,
    )
    result = runner.run()
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# qit new / qit list-templates
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Known scaffold templates (cookiecutter-based)
_SCAFFOLD_TEMPLATES = {
    "qitos_new_agent": "Scaffold a complete agent project with tests and eval config",
}

# Known method templates (paper reproduction / reference)
_METHOD_TEMPLATES = {
    "react": "ReAct — Reason+Act agent with scratchpad",
    "plan_act": "Plan-and-Act — separate planning and execution phases",
    "swe_agent": "SWE-Agent — software engineering agent",
    "voyager": "Voyager — open-ended exploration with skill library",
    "debate": "Debate — multi-agent debate for reasoning",
    "manager_worker": "Manager-Worker — orchestration with delegation",
    "planner_executor": "Planner-Executor — plan decomposition with execution",
    "lats": "LATS — Monte Carlo tree search with language evaluation",
    "moa": "MoA — parallel proposals and aggregation",
    "magentic_one": "Magentic-One — orchestrator with specialist workers",
}


def _list_templates_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="qit list-templates", description="List built-in agent templates"
    )
    parser.add_argument(
        "--type",
        choices=["scaffold", "method", "all"],
        default="all",
        help="Filter by template type (default: all)",
    )
    args = parser.parse_args(argv)

    if args.type in ("scaffold", "all"):
        print("Scaffold templates (cookiecutter-based, used with 'qit new'):")
        for name, desc in _SCAFFOLD_TEMPLATES.items():
            print(f"  {name:20s}  {desc}")
        if args.type == "all":
            print()

    if args.type in ("method", "all"):
        print("Method templates (paper reproduction / reference):")
        for name, desc in _METHOD_TEMPLATES.items():
            template_dir = _TEMPLATES_DIR / name
            marker = "" if template_dir.is_dir() else "  [not installed]"
            print(f"  {name:20s}  {desc}{marker}")

    return 0


def _new_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="qit new", description="Scaffold a new agent from a template"
    )
    parser.add_argument(
        "--template",
        default="qitos_new_agent",
        help="Template to use (default: qitos_new_agent)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to create the project in (default: current directory)",
    )
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="Use default values from cookiecutter.json without prompting",
    )
    # Pass-through cookiecutter parameters
    parser.add_argument("--agent-name", help="Agent name (cookiecutter param)")
    parser.add_argument("--agent-description", help="Agent description (cookiecutter param)")
    parser.add_argument("--author", help="Author name (cookiecutter param)")
    parser.add_argument("--default-model", help="Default model ID (cookiecutter param)")
    parser.add_argument("--max-steps", type=int, help="Max steps (cookiecutter param)")

    args = parser.parse_args(argv)

    template_dir = _TEMPLATES_DIR / args.template
    if not template_dir.is_dir():
        print(
            f"Error: Template '{args.template}' not found. "
            f"Run 'qit list-templates' to see available templates.",
            file=sys.stderr,
        )
        return 1

    # Check for cookiecutter.json to confirm it's a scaffold template
    if not (template_dir / "cookiecutter.json").exists():
        print(
            f"Error: Template '{args.template}' is not a scaffold template "
            f"(no cookiecutter.json). Method templates are reference-only.",
            file=sys.stderr,
        )
        return 1

    try:
        from cookiecutter.main import cookiecutter
    except ImportError:
        print(
            "Error: cookiecutter is required for 'qit new'. "
            "Install it with: pip install cookiecutter",
            file=sys.stderr,
        )
        return 1

    # Build extra_context from CLI args
    extra_context: dict[str, str] = {}
    if args.agent_name:
        extra_context["agent_name"] = args.agent_name
    if args.agent_description:
        extra_context["agent_description"] = args.agent_description
    if args.author:
        extra_context["author"] = args.author
    if args.default_model:
        extra_context["default_model"] = args.default_model
    if args.max_steps is not None:
        extra_context["max_steps"] = str(args.max_steps)

    try:
        result_dir = cookiecutter(
            str(template_dir),
            output_dir=args.output_dir,
            no_input=args.no_input or bool(extra_context),
            extra_context=extra_context or None,
        )
        print(f"Created agent project at: {result_dir}")
        return 0
    except Exception as exc:
        print(f"Error generating project: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
