"""Tau-Bench evaluation with QitOS (single task + full benchmark mode)."""

from __future__ import annotations

import argparse
import json
import os
import random
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from qitos import (
    Action,
    AgentModule,
    BenchmarkRunResult,
    Decision,
    ExperimentSpec,
    RunSpec,
    StateSchema,
    Task,
    TaskBudget,
    ToolRegistry,
)
from qitos.benchmark import TauBenchAdapter
from qitos.benchmark.tau_bench.port.types import Action as TauAction
from qitos.evaluate import EvaluationContext, EvaluationSuite
from qitos.kit import ReActTextParser, format_action, render_prompt
from qitos.kit.evaluate import DSLEvaluator, ModelBasedEvaluator, RuleBasedEvaluator
from qitos.kit.metric import (
    MeanStepsMetric,
    RewardAverageMetric,
    RewardPassHatMetric,
    RewardSuccessRateMetric,
    StopReasonDistributionMetric,
    is_successful_reward,
)
from qitos.metric import MetricInput, MetricRegistry
from qitos.models import OpenAICompatibleModel
from qitos.render import ClaudeStyleHook
from qitos.trace import TraceWriter

from ._shared import (
    build_example_specs,
    default_output_path,
    execute_example_jobs,
    print_benchmark_summary,
    print_single_result,
)

DEFAULT_MODEL_BASE_URL = "https://api.siliconflow.cn/v1/"
DEFAULT_MODEL_NAME = "Qwen/Qwen3-8B"
DEFAULT_THEME = "research"

SYSTEM_PROMPT = """You are a Tau-Bench task-solving agent.

Domain Wiki:
{wiki}

Policy Rules:
{rules}

Available tools:
{tool_schema}

Rules:
- Exactly one action per step.
- Use function-style action syntax.
- Use respond(content=...) when you want to talk to the user.
- Be concise and policy-compliant.

Output format:
Thought: <one-line reasoning>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <very short>
"""

_APPEND_LOCK = threading.Lock()


@dataclass
class TauState(StateSchema):
    current_observation: str = ""
    scratchpad: List[str] = field(default_factory=list)
    reward: float = 0.0
    done: bool = False


class TauActionTool:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        required: List[str],
        runner: Any,
    ):
        from qitos.core.tool import BaseTool, ToolPermission, ToolSpec

        class _Impl(BaseTool):
            def __init__(self):
                super().__init__(
                    ToolSpec(
                        name=name,
                        description=description,
                        parameters={k: v for k, v in (parameters or {}).items()},
                        required=list(required or []),
                        permissions=ToolPermission(),
                    )
                )

            def run(
                self, runtime_context: Optional[Dict[str, Any]] = None, **kwargs: Any
            ) -> Dict[str, Any]:
                return runner(name, kwargs)

        self.impl = _Impl()


class TauBenchAgent(AgentModule[TauState, Dict[str, Any], Action]):
    def __init__(self, llm: Any, tau_env: Any):
        self.tau_env = tau_env
        registry = ToolRegistry()

        for item in list(getattr(tau_env, "tools_info", []) or []):
            fn = dict(item.get("function", {}) or {})
            name = str(fn.get("name", "")).strip()
            if not name:
                continue
            desc = str(fn.get("description", ""))
            params = dict((fn.get("parameters") or {}).get("properties", {}) or {})
            req = list((fn.get("parameters") or {}).get("required", []) or [])
            wrapper = TauActionTool(name, desc, params, req, self._step_tool)
            registry.register(wrapper.impl)

        if "respond" not in registry.list_tools():
            wrapper = TauActionTool(
                "respond",
                "Respond to user with plain text content",
                {"content": {"type": "string", "description": "response text"}},
                ["content"],
                self._step_tool,
            )
            registry.register(wrapper.impl)

        super().__init__(
            tool_registry=registry, llm=llm, model_parser=ReActTextParser()
        )

    def init_state(self, task: str, **kwargs: Any) -> TauState:
        task_index = int(kwargs.get("task_index", 0))
        reset = self.tau_env.reset(task_index=task_index)
        obs = str(getattr(reset, "observation", ""))
        return TauState(
            task=task,
            max_steps=int(kwargs.get("max_steps", 30)),
            current_observation=obs,
        )

    def build_system_prompt(self, state: TauState) -> str | None:
        wiki = str(getattr(self.tau_env, "wiki", ""))
        rules = "\n".join(
            f"- {r}" for r in list(getattr(self.tau_env, "rules", []) or [])
        )
        return render_prompt(
            SYSTEM_PROMPT,
            {
                "wiki": wiki,
                "rules": rules,
                "tool_schema": (
                    self.tool_registry.get_tool_descriptions()
                    if self.tool_registry
                    else ""
                ),
            },
        )

    def prepare(self, state: TauState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Step: {state.current_step}/{state.max_steps}",
            f"Current observation: {state.current_observation}",
            f"Reward so far: {state.reward}",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self, state: TauState, observation: Dict[str, Any], decision: Decision[Action]
    ) -> TauState:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
        if action_results:
            first = (
                action_results[0]
                if isinstance(action_results[0], dict)
                else {"observation": str(action_results[0])}
            )
            state.scratchpad.append(f"Observation: {first.get('observation', first)}")
            state.current_observation = str(
                first.get("observation", state.current_observation)
            )
            state.reward = float(first.get("reward", state.reward or 0.0))
            state.done = bool(first.get("done", False))
            if state.done:
                state.final_result = str(first.get("observation", ""))
                state.metadata["tau_reward"] = state.reward
                state.metadata["tau_info"] = first.get("info", {})
        state.scratchpad = state.scratchpad[-40:]
        return state

    def _step_tool(self, action_name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        response = self.tau_env.step(TauAction(name=action_name, kwargs=kwargs))
        info = (
            response.info.model_dump()
            if hasattr(response.info, "model_dump")
            else dict(getattr(response, "info", {}) or {})
        )
        return {
            "status": "success",
            "action": action_name,
            "kwargs": kwargs,
            "observation": str(getattr(response, "observation", "")),
            "reward": float(getattr(response, "reward", 0.0)),
            "done": bool(getattr(response, "done", False)),
            "info": info,
        }


def _add_common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--workspace", default="./qitos_tau_workspace")
    ap.add_argument(
        "--model-base-url", default=os.getenv("OPENAI_BASE_URL", DEFAULT_MODEL_BASE_URL)
    )
    ap.add_argument("--api-key", default="")
    ap.add_argument(
        "--model-name", default=os.getenv("QITOS_MODEL", DEFAULT_MODEL_NAME)
    )
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--theme", default=DEFAULT_THEME)
    ap.add_argument("--trace-logdir", default="./runs")
    ap.add_argument("--trace-prefix", default="qitos")
    ap.add_argument("--disable-trace", action="store_true")
    ap.add_argument("--disable-render", action="store_true")


def _build_model(args: argparse.Namespace) -> OpenAICompatibleModel:
    api_key = (
        str(args.api_key).strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
        or os.getenv("QITOS_API_KEY", "").strip()
    )
    if not api_key:
        raise ValueError(
            "Missing API key. Set --api-key or OPENAI_API_KEY/QITOS_API_KEY."
        )
    return OpenAICompatibleModel(
        model=str(args.model_name),
        api_key=api_key,
        base_url=str(args.model_base_url) or None,
        temperature=float(args.temperature),
        max_tokens=int(args.max_tokens),
    )


def _setup_workspace(
    path: str,
) -> tuple[Path, Optional[tempfile.TemporaryDirectory[str]]]:
    if path:
        root = Path(path).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root, None
    temp_ctx: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
    return Path(temp_ctx.name), temp_ctx


def _make_trace_writer(args: argparse.Namespace, case_name: str) -> TraceWriter | None:
    if bool(args.disable_trace):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run_id = f"{args.trace_prefix}_{case_name}_{stamp}"
    return TraceWriter(
        output_dir=str(Path(args.trace_logdir).expanduser().resolve()),
        run_id=run_id,
        strict_validate=True,
        metadata={"model_id": str(args.model_name)},
    )


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _APPEND_LOCK, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        tid = str(obj.get("task_id", "")).strip()
        job_key = str(obj.get("_job_key", "")).strip()
        if job_key:
            done.add(job_key)
        elif tid:
            done.add(tid)
    return done


def _build_tau_env(args: argparse.Namespace, task_index: int | None = None) -> Any:
    from qitos.benchmark.tau_bench.runtime import get_tau_runtime_env

    return get_tau_runtime_env(
        env_name=args.tau_env, task_split=args.tau_split, task_index=task_index
    )


def _evaluate_one(
    task: Task, result: Any, include_model_judge: bool = False, llm: Any = None
) -> Dict[str, Any]:
    manifest = {
        "summary": {
            "stop_reason": result.state.stop_reason,
            "final_result": result.state.final_result,
            "steps": result.step_count,
        }
    }
    extras = {
        "reward": float(result.state.metadata.get("tau_reward", 0.0)),
        "tau_info": result.state.metadata.get("tau_info", {}),
    }

    evaluators = [
        RuleBasedEvaluator(name="tau_reward_rule", min_reward=1.0),
        DSLEvaluator(name="tau_done_dsl", expression="extras['reward'] >= 1.0"),
    ]
    if include_model_judge:
        evaluators.append(ModelBasedEvaluator(name="tau_model_judge", llm=llm))

    suite = EvaluationSuite(evaluators=evaluators, mode="all")
    suite_res = suite.evaluate(
        EvaluationContext(task=task, run=result, manifest=manifest, extras=extras)
    )
    return {
        "success": suite_res.success,
        "score": suite_res.score,
        "results": [
            {
                "name": r.name,
                "success": r.success,
                "score": r.score,
                "reasons": r.reasons,
            }
            for r in suite_res.results
        ],
    }


def _run_one_task(
    args: argparse.Namespace,
    adapter: TauBenchAdapter,
    idx: int,
    record: Dict[str, Any],
    root: Path,
    trial: int = 0,
    run_spec: RunSpec | None = None,
    experiment_spec: ExperimentSpec | None = None,
) -> Dict[str, Any]:
    started = time.time()
    task = adapter.to_task(record, split=args.tau_split, idx=idx)
    task.budget = TaskBudget(max_steps=int(args.max_steps))

    tau_env = _build_tau_env(args, task_index=idx)
    model = _build_model(args)
    agent = TauBenchAgent(llm=model, tau_env=tau_env)

    trace_writer = _make_trace_writer(
        args, f"tau_{args.tau_env}_{idx:05d}_trial{trial}"
    )
    render = (
        None
        if args.disable_render
        else ClaudeStyleHook(
            output_jsonl=str(root / f"render_events_{idx:05d}_trial{trial}.jsonl"),
            theme=args.theme,
        )
    )

    error_msg = None
    token_usage = 0
    try:
        result = agent.run(
            task=task,
            return_state=True,
            max_steps=int(args.max_steps),
            task_index=idx,
            trace=trace_writer,
            render=render,
            run_spec=run_spec,
            experiment_spec=experiment_spec,
        )
        eval_out = _evaluate_one(
            task=task,
            result=result,
            include_model_judge=bool(args.enable_model_judge),
            llm=model,
        )
        reward = float(result.state.metadata.get("tau_reward", 0.0))
        stop_reason = result.state.stop_reason
        steps = result.step_count
        token_usage = int(
            (
                (result.task_result.metrics if result.task_result is not None else {})
                or {}
            ).get("token_usage", 0)
        )
    except Exception as exc:
        reward = 0.0
        stop_reason = "exception"
        steps = 0
        eval_out = {"success": False, "score": 0.0, "results": []}
        error_msg = str(exc)

    return {
        "task_id": task.id,
        "idx": idx,
        "trial": trial,
        "benchmark": "tau-bench",
        "env": args.tau_env,
        "split": args.tau_split,
        "prediction": None,
        "reward": reward,
        "success": bool(eval_out.get("success", False)),
        "eval_score": float(eval_out.get("score", 0.0)),
        "eval_details": eval_out.get("results", []),
        "stop_reason": stop_reason,
        "steps": steps,
        "token_usage": token_usage,
        "cost": 0.0,
        "trace_run_dir": (
            str(trace_writer.run_dir) if trace_writer is not None else None
        ),
        "error": error_msg,
        "started_at": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "latency_seconds": round(time.time() - started, 3),
    }


def _tau_runner(
    *,
    task: Task,
    record: Dict[str, Any],
    idx: int,
    trial: int,
    root: Path,
    args: argparse.Namespace,
    run_spec: RunSpec,
    experiment_spec: ExperimentSpec,
    **_: Any,
) -> BenchmarkRunResult:
    row = _run_one_task(
        args=args,
        adapter=TauBenchAdapter(env_name=args.tau_env, task_split=args.tau_split),
        idx=idx,
        record=record,
        root=root,
        trial=trial,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
    )
    return BenchmarkRunResult(
        task_id=str(task.id),
        benchmark="tau-bench",
        split=str(args.tau_split),
        prediction=row.get("prediction"),
        success=bool(row.get("success", False)),
        stop_reason=str(row.get("stop_reason") or ""),
        steps=int(row.get("steps", 0)),
        latency_seconds=float(row.get("latency_seconds", 0.0)),
        token_usage=int(row.get("token_usage", 0)),
        cost=float(row.get("cost", 0.0)),
        trace_run_dir=row.get("trace_run_dir"),
        run_spec_ref=run_spec.fingerprint(),
        metadata={
            "reward": row.get("reward"),
            "eval_score": row.get("eval_score"),
            "eval_details": row.get("eval_details"),
            "env": row.get("env"),
            "trial": trial,
            "idx": idx,
        },
    )


def _print_metrics(rows: List[Dict[str, Any]]) -> None:
    metric_inputs = [
        MetricInput(
            task_id=str(r.get("task_id", "")),
            trial=int(r.get("trial", 0)),
            success=is_successful_reward(float(r.get("reward", 0.0))),
            reward=float(r.get("reward", 0.0)),
            steps=int(r.get("steps", 0)),
            latency_seconds=float(r.get("latency_seconds", 0.0)),
            stop_reason=str(r.get("stop_reason", "")),
            payload=r,
        )
        for r in rows
    ]
    registry = MetricRegistry(
        [
            RewardAverageMetric(),
            RewardSuccessRateMetric(),
            RewardPassHatMetric(),
            MeanStepsMetric(),
            StopReasonDistributionMetric(),
        ]
    )
    reports = {r.name: r for r in registry.compute_all(metric_inputs)}
    pass_hat_ks = (
        reports["reward_pass_hat"].value if "reward_pass_hat" in reports else {}
    )

    print("[Tau-Bench] Metrics (aligned with tau-bench run.py)")
    print(
        f"- avg_reward: {reports['avg_reward'].value if 'avg_reward' in reports else 0.0}"
    )
    print("- pass^k:")
    for k in sorted(pass_hat_ks.keys()):
        print(f"  - k={k}: {pass_hat_ks[k]}")
    for rep in reports.values():
        print(f"- {rep.name}: {rep.value}")


def _run_full(
    args: argparse.Namespace,
    adapter: TauBenchAdapter,
    records: List[Dict[str, Any]],
    root: Path,
) -> None:
    start_idx = max(0, int(args.start_index))
    end_idx = (
        len(records)
        if int(args.end_index) < 0
        else min(len(records), int(args.end_index))
    )
    indices = list(range(start_idx, end_idx))
    if int(args.limit) > 0:
        indices = indices[: int(args.limit)]

    all_jobs: List[tuple[int, int]] = []
    for trial in range(int(args.num_trials)):
        seq = list(indices)
        if bool(args.shuffle):
            rnd = random.Random(int(args.seed) + trial)
            rnd.shuffle(seq)
        for idx in seq:
            all_jobs.append((trial, idx))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = (
        Path(args.output_jsonl).expanduser().resolve()
        if args.output_jsonl
        else (root / f"tau_bench_{args.tau_env}_{args.tau_split}_{stamp}.jsonl")
    )

    done = _read_done(out_path) if args.resume else set()
    jobs = [(t, i) for (t, i) in all_jobs if f"{t}:{i}" not in done]
    print(
        f"[Tau-Bench] env={args.tau_env} split={args.tau_split} total_jobs={len(all_jobs)} pending={len(jobs)}"
    )

    rows: List[Dict[str, Any]] = []
    if int(args.concurrency) <= 1:
        for n, (trial, idx) in enumerate(jobs, start=1):
            row = _run_one_task(
                args=args,
                adapter=adapter,
                idx=idx,
                record=records[idx],
                root=root,
                trial=trial,
            )
            _append_jsonl(out_path, {**row, "_job_key": f"{trial}:{idx}"})
            rows.append(row)
            print(
                f"[Tau-Bench] {n}/{len(jobs)} trial={trial} idx={idx} reward={row['reward']} success={row['success']}"
            )
    else:
        with ThreadPoolExecutor(max_workers=int(args.concurrency)) as ex:
            futs = [
                ex.submit(
                    _run_one_task,
                    args=args,
                    adapter=adapter,
                    idx=idx,
                    record=records[idx],
                    root=root,
                    trial=trial,
                )
                for (trial, idx) in jobs
            ]
            for n, fut in enumerate(as_completed(futs), start=1):
                row = fut.result()
                _append_jsonl(
                    out_path, {**row, "_job_key": f"{row['trial']}:{row['idx']}"}
                )
                rows.append(row)
                print(
                    f"[Tau-Bench] {n}/{len(jobs)} trial={row['trial']} idx={row['idx']} "
                    f"reward={row['reward']} success={row['success']}"
                )

    print("output_jsonl:", out_path)
    _print_metrics(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    _add_common_args(ap)
    ap.add_argument("--tau-env", default="retail", choices=["retail", "airline"])
    ap.add_argument("--tau-split", default="test", choices=["train", "dev", "test"])
    ap.add_argument("--task-index", type=int, default=0)
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--run-all", action="store_true")
    ap.add_argument("--start-index", type=int, default=0)
    ap.add_argument("--end-index", type=int, default=-1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--num-trials", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--seed", type=int, default=10)
    ap.add_argument("--output-jsonl", default="")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--user-strategy", default="llm")
    ap.add_argument("--user-model", default="gpt-4o")
    ap.add_argument("--user-model-provider", default="openai")
    ap.add_argument("--enable-model-judge", action="store_true")
    args = ap.parse_args()

    root, temp_ctx = _setup_workspace(args.workspace)
    adapter = TauBenchAdapter(env_name=args.tau_env, task_split=args.tau_split)
    records = adapter.load_records(env_name=args.tau_env, split=args.tau_split)
    if not records:
        raise RuntimeError("No Tau-Bench tasks loaded.")

    if args.run_all:
        tasks = adapter.to_tasks(records, split=args.tau_split)
        run_spec, experiment_spec = build_example_specs(
            benchmark="tau-bench",
            split=args.tau_split,
            model_name=args.model_name,
            trace_logdir=args.trace_logdir,
            parser_name="ReActTextParser",
            toolset_name="ToolRegistry",
            subset=args.tau_env,
            seed=int(args.seed),
            limit=int(args.limit) if int(args.limit) > 0 else None,
            workspace=str(root),
            metadata={
                "example_entrypoint": "examples.benchmarks.tau_bench_eval",
            },
        )
        start_idx = max(0, int(args.start_index))
        end_idx = (
            len(records)
            if int(args.end_index) < 0
            else min(len(records), int(args.end_index))
        )
        indices = list(range(start_idx, end_idx))
        if int(args.limit) > 0:
            indices = indices[: int(args.limit)]
        jobs: List[Dict[str, Any]] = []
        for trial in range(int(args.num_trials)):
            seq = list(indices)
            if bool(args.shuffle):
                rnd = random.Random(int(args.seed) + trial)
                rnd.shuffle(seq)
            for idx in seq:
                jobs.append(
                    {
                        "task": tasks[idx],
                        "record": records[idx],
                        "idx": idx,
                        "trial": trial,
                        "root": root,
                        "args": args,
                        "job_key": f"{trial}:{idx}",
                    }
                )
        output_path = (
            Path(args.output_jsonl).expanduser().resolve()
            if args.output_jsonl
            else default_output_path(
                root,
                benchmark=f"tau_bench_{args.tau_env}",
                split=args.tau_split,
            )
        )
        rows = execute_example_jobs(
            jobs=jobs,
            runner=_tau_runner,
            output_path=output_path,
            run_spec=run_spec,
            experiment_spec=experiment_spec,
            concurrency=int(args.concurrency),
            resume=bool(args.resume),
        )
        print(f"output_jsonl: {output_path}")
        print_benchmark_summary(rows)
    else:
        idx = max(0, min(int(args.task_index), len(records) - 1))
        run_spec, experiment_spec = build_example_specs(
            benchmark="tau-bench",
            split=args.tau_split,
            model_name=args.model_name,
            trace_logdir=args.trace_logdir,
            parser_name="ReActTextParser",
            toolset_name="ToolRegistry",
            subset=args.tau_env,
            seed=int(args.seed),
            workspace=str(root),
            metadata={
                "example_entrypoint": "examples.benchmarks.tau_bench_eval",
            },
        )
        row = _tau_runner(
            task=adapter.to_task(records[idx], split=args.tau_split, idx=idx),
            record=records[idx],
            idx=idx,
            trial=0,
            root=root,
            args=args,
            run_spec=run_spec,
            experiment_spec=experiment_spec,
        )
        print("workspace:", root)
        print_single_result(row)

    if temp_ctx is not None:
        temp_ctx.cleanup()


if __name__ == "__main__":
    main()
