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
    parser = argparse.ArgumentParser(
        prog="qit", description="QitOS CLI for demos, benchmarks, and developer workflows"
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("demo", help="Run packaged demos and quickstarts")
    subparsers.add_parser("skill", help="Manage third-party skills")
    subparsers.add_parser("bench", help="Unified benchmark CLI")
    subparsers.add_parser("experiment", help="Run parameter-sweep experiments")
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

    args = parser.parse_args(argv)
    if args.command == "run":
        return _bench_run(args)
    if args.command == "eval":
        return _bench_eval(args)
    if args.command == "replay":
        return _bench_replay(args)
    if args.command == "export":
        return _bench_export(args)
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


if __name__ == "__main__":
    sys.exit(main())
