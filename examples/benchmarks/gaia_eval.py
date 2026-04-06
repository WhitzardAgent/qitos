"""OpenDeepResearch-style GAIA benchmark runner built with QitOS."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from qitos import Action, AgentModule, Decision, EnvSpec, StateSchema, Task, TaskBudget, ToolRegistry
from qitos.benchmark import GaiaAdapter
from qitos.kit import ReActTextParser, TextWebEnv, format_action, render_prompt
from qitos.kit.tool import (
    ArchiveSearch,
    FindInPage,
    FindNext,
    ListFiles,
    PageDown,
    PageUp,
    ReadFile,
    RunCommand,
    VisitURL,
    WebSearch,
    WriteFile,
)
from qitos.models import OpenAICompatibleModel
from qitos.render import ClaudeStyleHook
from qitos.trace import TraceWriter

DEFAULT_MODEL_BASE_URL = "https://api.siliconflow.cn/v1/"
DEFAULT_MODEL_NAME = "Qwen/Qwen3-8B"
DEFAULT_THEME = "research"

SYSTEM_PROMPT = """You are an OpenDeepResearch benchmark agent.

Rules:
- Use tool calls with function syntax only, exactly one tool call per step.
- Prefer this loop: web_search -> visit_url -> page_down/find_in_page -> find_next.
- Keep evidence snippets in your scratchpad and verify before final answer.
- If attachments are provided, inspect them before concluding.

Tool schema:
{tool_schema}

Output format:
Thought: <short reasoning>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <answer only>
"""

_APPEND_LOCK = threading.Lock()


@dataclass
class ODRGaiaState(StateSchema):
    scratchpad: List[str] = field(default_factory=list)
    task_payload: Dict[str, Any] = field(default_factory=dict)


class OpenDeepResearchGaiaAgent(AgentModule[ODRGaiaState, Dict[str, Any], Action]):
    name = "open_deep_research_gaia"

    def __init__(self, llm: Any, workspace_root: str):
        registry = ToolRegistry()
        registry.register(WebSearch())
        registry.register(VisitURL())
        registry.register(PageUp())
        registry.register(PageDown())
        registry.register(FindInPage())
        registry.register(FindNext())
        registry.register(ArchiveSearch())
        registry.register(ReadFile(root_dir=workspace_root))
        registry.register(ListFiles(root_dir=workspace_root))
        registry.register(WriteFile(root_dir=workspace_root))
        registry.register(RunCommand(cwd=workspace_root))
        super().__init__(tool_registry=registry, llm=llm, model_parser=ReActTextParser())

    def init_state(self, task: str, **kwargs: Any) -> ODRGaiaState:
        return ODRGaiaState(
            task=task,
            max_steps=int(kwargs.get("max_steps", 16)),
            task_payload=dict(kwargs.get("task_payload", {}) or {}),
        )

    def build_system_prompt(self, state: ODRGaiaState) -> str | None:
        return render_prompt(SYSTEM_PROMPT, {"tool_schema": self.tool_registry.get_tool_descriptions()})

    def prepare(self, state: ODRGaiaState) -> str:
        payload = dict(getattr(state, "task_payload", {}) or {})
        lines = [
            f"Task: {payload.get('question', state.task)}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        rationales = _recent_rationales_from_scratchpad(state.scratchpad, max_items=6)
        if rationales:
            lines.append("Recent rationale:")
            lines.extend(f"- {x}" for x in rationales)
        attachments = payload.get("attachments") or []
        if attachments:
            lines.append("Attachments:")
            lines.extend(f"- {x}" for x in attachments)
        if state.scratchpad:
            lines.append("Recent Evidence:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(self, state: ODRGaiaState, observation: Dict[str, Any], decision: Decision[Action]) -> ODRGaiaState:
        action_results = observation.get("action_results", []) if isinstance(observation, dict) else []
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
        if action_results:
            state.scratchpad.append(f"Observation: {action_results[0]}")
        state.scratchpad = state.scratchpad[-40:]
        return state


def _add_common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--workspace", default="./qitos_gaia_workspace")
    ap.add_argument("--model-base-url", default=os.getenv("OPENAI_BASE_URL", DEFAULT_MODEL_BASE_URL))
    ap.add_argument("--api-key", default="")
    ap.add_argument("--model-name", default=os.getenv("QITOS_MODEL", DEFAULT_MODEL_NAME))
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--theme", default=DEFAULT_THEME)
    ap.add_argument("--trace-logdir", default="./runs")
    ap.add_argument("--trace-prefix", default="qitos")
    ap.add_argument("--disable-trace", action="store_true")
    ap.add_argument("--disable-render", action="store_true")


def _build_model(args: argparse.Namespace) -> OpenAICompatibleModel:
    api_key = str(args.api_key).strip() or os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("QITOS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Missing API key. Set --api-key or OPENAI_API_KEY/QITOS_API_KEY.")
    return OpenAICompatibleModel(
        model=str(args.model_name),
        api_key=api_key,
        base_url=str(args.model_base_url) or None,
        temperature=float(args.temperature),
        max_tokens=int(args.max_tokens),
    )


def _setup_workspace(path: str) -> tuple[Path, Optional[tempfile.TemporaryDirectory[str]]]:
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


def _recent_rationales_from_scratchpad(scratchpad: List[str], max_items: int = 6) -> List[str]:
    out: List[str] = []
    for item in reversed(scratchpad or []):
        text = str(item).strip()
        if not text:
            continue
        low = text.lower()
        if low.startswith("thought:"):
            out.append(text.split(":", 1)[1].strip())
        elif low.startswith("rationale:"):
            out.append(text.split(":", 1)[1].strip())
        if len(out) >= max(1, int(max_items)):
            break
    out.reverse()
    return out


def _first_non_empty(record: Mapping[str, Any], keys: Sequence[str]) -> Optional[Any]:
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _normalize_filename(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return clean.strip("_") or "task"


def _materialize_attachments(task: Task, workspace_root: Path) -> None:
    copied: List[str] = []
    for res in task.resources:
        if res.kind != "file" or not res.path:
            continue
        src = Path(res.path)
        if not src.exists() or src.is_dir():
            continue
        dst = workspace_root / "attachments" / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        rel = str(dst.relative_to(workspace_root))
        res.path = rel
        copied.append(rel)
    task.inputs["attachments"] = copied


def _load_gaia_records(args: argparse.Namespace) -> tuple[GaiaAdapter, list[dict[str, Any]]]:
    adapter = GaiaAdapter(local_dir=args.gaia_local_dir)
    if args.gaia_download_snapshot:
        adapter.snapshot_dataset(
            use_raw_dataset=bool(args.gaia_use_raw_dataset),
            local_dir=args.gaia_local_dir,
            hf_token=os.getenv("HF_TOKEN", "").strip() or None,
        )

    if args.gaia_from_local:
        records = adapter.load_local_records(split=args.gaia_split, local_dir=args.gaia_local_dir)
    else:
        records = adapter.load_huggingface_records(
            split=args.gaia_split,
            subset=args.gaia_subset or None,
            use_annotated_dataset=bool(args.gaia_use_annotated),
        )
    return adapter, records


def _build_task(adapter: GaiaAdapter, record: Mapping[str, Any], split: str, idx: int, workspace_root: Path, max_steps: int) -> Task:
    task = adapter.to_task(record, split=split, idx=idx)
    task.env_spec = EnvSpec(type="text_web_env", config={"workspace_root": str(workspace_root)})
    task.budget = TaskBudget(max_steps=max_steps)
    _materialize_attachments(task, workspace_root)
    return task


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _APPEND_LOCK, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_done_task_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            task_id = str(row.get("task_id", "")).strip()
            if task_id:
                done.add(task_id)
    return done


def _run_one_record(args: argparse.Namespace, adapter: GaiaAdapter, record: Mapping[str, Any], idx: int, root: Path) -> Dict[str, Any]:
    started = time.time()
    raw_id = _first_non_empty(record, ["task_id", "id", "sample_id", "qid"])
    task_id_seed = str(raw_id) if raw_id is not None else f"{args.gaia_split}_{idx:05d}"
    task_workspace = root / "tasks" / f"{idx:05d}_{_normalize_filename(task_id_seed)}"
    task_workspace.mkdir(parents=True, exist_ok=True)

    task = _build_task(
        adapter=adapter,
        record=record,
        split=args.gaia_split,
        idx=idx,
        workspace_root=task_workspace,
        max_steps=int(args.max_steps),
    )

    model = _build_model(args)
    agent = OpenDeepResearchGaiaAgent(llm=model, workspace_root=str(task_workspace))
    trace_writer = _make_trace_writer(args, f"gaia_odr_{_normalize_filename(task.id)}")
    render = None if args.disable_render else ClaudeStyleHook(
        output_jsonl=str(task_workspace / "render_events.jsonl"),
        theme=args.theme,
    )

    error_msg = None
    try:
        result = agent.run(
            task=task,
            return_state=True,
            max_steps=int(args.max_steps),
            task_payload=task.inputs,
            env=TextWebEnv(workspace_root=str(task_workspace)),
            trace=trace_writer,
            render=render,
        )
        final_result = result.state.final_result
        stop_reason = result.state.stop_reason
        step_count = result.step_count
    except Exception as exc:
        final_result = None
        stop_reason = "exception"
        step_count = 0
        error_msg = str(exc)

    answer_path = task_workspace / "gaia_answer.txt"
    answer_path.write_text(str(final_result or ""), encoding="utf-8")

    ref_answer = _first_non_empty(record, ["true_answer", "Final answer", "final_answer", "answer", "gold_answer"])
    question = _first_non_empty(record, ["question", "Question", "prompt", "problem", "query", "instruction"])

    return {
        "task_id": task.id,
        "source_task_id": raw_id,
        "idx": idx,
        "split": args.gaia_split,
        "question": question,
        "reference_answer": ref_answer,
        "prediction": final_result,
        "stop_reason": stop_reason,
        "steps": step_count,
        "error": error_msg,
        "workspace": str(task_workspace),
        "answer_file": str(answer_path),
        "trace_run_dir": str(trace_writer.run_dir) if trace_writer is not None else None,
        "started_at": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "latency_seconds": round(time.time() - started, 3),
    }


def _run_full_benchmark(args: argparse.Namespace, adapter: GaiaAdapter, records: list[dict[str, Any]], root: Path) -> None:
    selected: list[tuple[int, dict[str, Any]]] = []
    start_idx = max(0, int(args.start_index))
    for i, row in enumerate(records):
        if i < start_idx:
            continue
        selected.append((i, row))
        if int(args.limit) > 0 and len(selected) >= int(args.limit):
            break

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output_jsonl).expanduser().resolve() if args.output_jsonl else (root / f"gaia_{args.gaia_split}_{stamp}.jsonl")
    done_ids = _read_done_task_ids(output_path) if args.resume else set()

    jobs: list[tuple[int, dict[str, Any]]] = []
    for idx, row in selected:
        task_id_raw = _first_non_empty(row, ["task_id", "id", "sample_id", "qid"])
        fallback_id = f"gaia_{args.gaia_split}_{idx:05d}"
        task_id = str(task_id_raw).strip() if task_id_raw else fallback_id
        if task_id in done_ids:
            continue
        jobs.append((idx, row))

    print(f"[GAIA] split={args.gaia_split} total_loaded={len(records)} selected={len(selected)} to_run={len(jobs)}")
    if not jobs:
        print("[GAIA] no pending tasks.")
        return

    ok_count = 0
    fail_count = 0
    started = time.time()
    workers = max(1, int(args.concurrency))

    if workers == 1:
        for n, (idx, row) in enumerate(jobs, start=1):
            entry = _run_one_record(args=args, adapter=adapter, record=row, idx=idx, root=root)
            _append_jsonl(output_path, entry)
            if entry.get("error"):
                fail_count += 1
            else:
                ok_count += 1
            print(f"[GAIA] {n}/{len(jobs)} task_id={entry['task_id']} stop={entry['stop_reason']} err={bool(entry.get('error'))}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as exe:
            futs = [
                exe.submit(_run_one_record, args=args, adapter=adapter, record=row, idx=idx, root=root)
                for idx, row in jobs
            ]
            for n, fut in enumerate(as_completed(futs), start=1):
                entry = fut.result()
                _append_jsonl(output_path, entry)
                if entry.get("error"):
                    fail_count += 1
                else:
                    ok_count += 1
                print(f"[GAIA] {n}/{len(jobs)} task_id={entry['task_id']} stop={entry['stop_reason']} err={bool(entry.get('error'))}")

    duration = round(time.time() - started, 2)
    print("[GAIA] run complete")
    print("output_jsonl:", output_path)
    print("ok:", ok_count, "failed:", fail_count, "duration_s:", duration)


def main() -> None:
    ap = argparse.ArgumentParser()
    _add_common_args(ap)
    ap.add_argument("--gaia-split", default="validation")
    ap.add_argument("--gaia-index", type=int, default=0)
    ap.add_argument("--gaia-subset", default="")
    ap.add_argument("--gaia-local-dir", default="data/gaia")
    ap.add_argument("--gaia-from-local", action="store_true")
    ap.add_argument("--gaia-download-snapshot", action="store_true")
    ap.add_argument("--gaia-use-raw-dataset", action="store_true")
    ap.add_argument("--gaia-use-annotated", action="store_true")
    ap.add_argument("--max-steps", type=int, default=16)
    ap.add_argument("--run-all", action="store_true", help="Run benchmark mode on the whole selected GAIA split")
    ap.add_argument("--limit", type=int, default=0, help="Max number of records to run in benchmark mode (0 means all)")
    ap.add_argument("--start-index", type=int, default=0, help="Start index in selected split for benchmark mode")
    ap.add_argument("--concurrency", type=int, default=1, help="Worker count for benchmark mode")
    ap.add_argument("--output-jsonl", default="", help="Optional output jsonl path for benchmark mode")
    ap.add_argument("--resume", action="store_true", help="Skip already finished task_ids in output jsonl")
    args = ap.parse_args()

    root, temp_ctx = _setup_workspace(args.workspace)
    adapter, records = _load_gaia_records(args)
    if not records:
        raise RuntimeError("No GAIA records loaded.")

    if args.run_all:
        _run_full_benchmark(args=args, adapter=adapter, records=records, root=root)
    else:
        idx = max(0, min(int(args.gaia_index), len(records) - 1))
        entry = _run_one_record(args=args, adapter=adapter, record=records[idx], idx=idx, root=root)
        print("workspace:", root)
        print("task_id:", entry["task_id"])
        print("final_result:", entry["prediction"])
        print("stop_reason:", entry["stop_reason"])
        print("answer_file:", entry["answer_file"])
        if entry["trace_run_dir"]:
            print("trace_run_dir:", entry["trace_run_dir"])

    if temp_ctx is not None:
        temp_ctx.cleanup()


if __name__ == "__main__":
    main()
