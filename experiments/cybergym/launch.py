"""Launch CyberGym experiments from YAML configs.

Usage:
    python -m experiments.cybergym.launch --config experiments/cybergym/configs/v1_luke.yaml
    python -m experiments.cybergym.launch --config experiments/cybergym/configs/v1_luke.yaml --resume
    python -m experiments.cybergym.launch --config experiments/cybergym/configs/ --all
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _resolve_env_vars(text: str) -> str:
    """Replace ``${VAR}`` patterns with environment variable values."""

    def _replace(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))

    return re.sub(r"\$\{(\w+)\}", _replace, text)


def load_config(path: Path) -> Dict[str, Any]:
    """Load a YAML config with ``${VAR}`` interpolation."""
    text = path.read_text(encoding="utf-8")
    text = _resolve_env_vars(text)
    config = yaml.safe_load(text)
    name = config["experiment"]["name"]
    config["experiment"]["resolved_name"] = name
    # Resolve EXPERIMENT_NAME in all string values
    _deep_replace(config, "${EXPERIMENT_NAME}", name)
    return config


def _deep_replace(obj: Any, pattern: str, replacement: str) -> None:
    """In-place replace ``pattern`` with ``replacement`` in all string values."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                obj[k] = v.replace(pattern, replacement)
            elif isinstance(v, (dict, list)):
                _deep_replace(v, pattern, replacement)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = v.replace(pattern, replacement)
            elif isinstance(v, (dict, list)):
                _deep_replace(v, pattern, replacement)


# ---------------------------------------------------------------------------
# Task ID loading
# ---------------------------------------------------------------------------

def _load_task_ids_from_file(path: Path, limit: int = 0) -> List[str]:
    items = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return items[: int(limit)] if int(limit) > 0 else items


def _load_task_ids_from_scan(data_dir: Path, limit: int = 0, start_index: int = 0) -> List[str]:
    arvo_root = data_dir / "arvo"
    task_dirs = sorted(
        (p for p in arvo_root.iterdir() if p.is_dir()),
        key=lambda p: int(p.name) if p.name.isdigit() else p.name,
    )
    selected = task_dirs[int(start_index) :]
    if int(limit) > 0:
        selected = selected[: int(limit)]
    return [f"arvo:{p.name}" for p in selected]


def _resolve_task_ids(tasks_cfg: Dict[str, Any]) -> List[str]:
    source = tasks_cfg.get("source", "file")
    limit = int(tasks_cfg.get("limit", 0))
    if source == "file":
        return _load_task_ids_from_file(
            Path(tasks_cfg["file"]).expanduser().resolve(), limit=limit
        )
    elif source == "list":
        ids = list(tasks_cfg.get("ids", []))
        return ids[: int(limit)] if int(limit) > 0 else ids
    elif source == "scan":
        return _load_task_ids_from_scan(
            Path(tasks_cfg["data_dir"]).expanduser().resolve(),
            limit=limit,
            start_index=int(tasks_cfg.get("start_index", 0)),
        )
    else:
        raise ValueError(f"Unknown task source: {source}")


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(config: Dict[str, Any], resume: bool = False) -> List[Any]:
    """Run a single experiment from a parsed config dict."""
    from cybergym_agent.benchmark.adapter import load_cybergym_tasks
    from cybergym_agent.benchmark.runner import run_cybergym_task
    from qitos.core.spec import ExperimentSpec, RunSpec
    from qitos.recipes.benchmarks._shared import (
        build_example_specs,
        execute_example_jobs,
        print_benchmark_summary,
    )

    exp = config["experiment"]
    model = config["model"]
    agent_cfg = config["agent"]
    env_cfg = config["environment"]
    tasks_cfg = config["tasks"]
    output_cfg = config["output"]

    name = exp["resolved_name"]
    out_dir = Path(output_cfg["dir"]).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save config snapshot + metadata
    config_snapshot = out_dir / "config.yaml"
    if not config_snapshot.exists():
        shutil.copy2(config.get("_source_path", ""), config_snapshot)

    meta_path = out_dir / "meta.json"
    meta = {
        "experiment_name": name,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_qitos": _git_commit(Path(__file__).resolve().parents[2]),
        "config_file": str(config.get("_source_path", "")),
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    # Resolve task IDs
    task_ids = _resolve_task_ids(tasks_cfg)
    tasks = load_cybergym_tasks(task_ids=task_ids, difficulty=tasks_cfg.get("difficulty", "level1"))
    jobs = [{"task": t, "job_key": t.id} for t in tasks]

    # Build RunSpec + ExperimentSpec
    trace_logdir = str(env_cfg.get("trace_logdir", str(out_dir / "traces")))
    workspace = str(env_cfg.get("workspace", str(out_dir / "workspace")))

    run_spec, experiment_spec = build_example_specs(
        benchmark=exp.get("benchmark", "cybergym"),
        split=exp.get("split", "level1"),
        model_name=model.get("model_name"),
        trace_logdir=trace_logdir,
        parser_name="JsonDecisionParser",
        toolset_name="cybergym_agent",
        limit=len(jobs),
        workspace=workspace,
        metadata={
            "recipe": "cybergym_agent_batch",
            "max_steps": int(agent_cfg.get("max_steps", 1_000_000)),
            "max_runtime_seconds": float(agent_cfg.get("max_runtime_seconds", 7200)),
        },
    )

    # Populate environment
    run_spec.environment = dict(run_spec.environment or {})
    run_spec.environment.update(
        {
            "data_dir": env_cfg.get("data_dir", ""),
            "server": env_cfg.get("server", ""),
            "base_url": model.get("base_url", ""),
            "api_key": model.get("api_key", ""),
            "trace_logdir": trace_logdir,
            "workspace": workspace,
            "trace_prefix": env_cfg.get("trace_prefix", f"qitos_{name}"),
        }
    )

    output_path = out_dir / output_cfg.get(
        "filename", f"cybergym_{name}.jsonl"
    )

    rows = execute_example_jobs(
        jobs=jobs,
        runner=lambda **kw: run_cybergym_task(
            task=kw["task"],
            run_spec=kw["run_spec"],
            experiment_spec=kw["experiment_spec"],
        ),
        output_path=output_path,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=max(1, int(exp.get("concurrency", 1))),
        resume=resume,
    )

    print_benchmark_summary(rows)
    print(f"OUTPUT_JSONL={output_path}")

    # Update meta with completion
    meta["completed_at"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta, indent=2))

    return rows


# ---------------------------------------------------------------------------
# Launch with grading server
# ---------------------------------------------------------------------------

def launch_with_server(config: Dict[str, Any], resume: bool = False) -> None:
    """Launch an experiment with its own grading server (mirrors launch.sh behavior)."""
    server_cfg = config.get("server", {})
    if not server_cfg:
        run_experiment(config, resume=resume)
        return

    name = config["experiment"]["resolved_name"]
    port = server_cfg.get("port")
    binary_dir = server_cfg.get("binary_dir", "")

    # Start grading server in tmux
    tmux_session = f"jcy-{name}"
    # ... server startup logic (to be filled with actual server command) ...

    # Update server URL in config
    config["environment"]["server"] = f"http://127.0.0.1:{port}"

    try:
        run_experiment(config, resume=resume)
    finally:
        # Kill tmux session
        subprocess.run(["tmux", "kill-session", "-t", tmux_session], capture_output=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_commit(repo_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch CyberGym experiments from YAML configs."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to a YAML config file or directory of configs (--all required for dir).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing results (skip completed tasks).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="When --config is a directory, run all YAML files in it.",
    )
    parser.add_argument(
        "--with-server",
        action="store_true",
        help="Start a grading server for each experiment (mirrors launch.sh).",
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()

    if config_path.is_dir():
        if not args.all:
            print("Error: --config points to a directory. Use --all to run all configs.", file=sys.stderr)
            return 1
        configs = sorted(config_path.glob("*.yaml"))
        if not configs:
            print(f"No YAML configs found in {config_path}", file=sys.stderr)
            return 1
    else:
        configs = [config_path]

    for cfg_path in configs:
        print(f"\n{'='*60}\nLoading config: {cfg_path.name}\n{'='*60}")
        config = load_config(cfg_path)
        config["_source_path"] = str(cfg_path)

        if args.with_server:
            launch_with_server(config, resume=args.resume)
        else:
            run_experiment(config, resume=args.resume)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
