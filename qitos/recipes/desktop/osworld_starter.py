"""Canonical desktop-starter recipe for QitOS computer-use research."""

from __future__ import annotations

import argparse
import base64
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from qitos import (
    Action,
    AgentModule,
    BenchmarkRunResult,
    Decision,
    EngineResult,
    EnvSpec,
    ExperimentSpec,
    RunSpec,
    StateSchema,
    Task,
    TaskBudget,
    ToolResult,
)
from qitos.engine.critic import Critic
from qitos.harness import build_harness_policy, build_model_for_preset, resolve_family_preset
from qitos.kit.planning.state_ops import format_action
from qitos.kit.prompts.computer_use import (
    computer_use_persona_prompt,
    computer_use_task_policy,
)
from qitos.kit.toolset.computer_use import ComputerUseToolSet


TASK_TEXT = "Open the target desktop workflow, interact with the visible UI, and report the grounded outcome."
WORKSPACE = Path("./playground/openai_cua_agent")
SCREENSHOT_FILE = "desktop.png"
MODEL_NAME = os.getenv("QITOS_MODEL", "qwen-plus")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_PROTOCOL = os.getenv("QITOS_CUA_PROTOCOL", "desktop_actions_json_v1")
DEFAULT_OBSERVATION_MODE = os.getenv("QITOS_CUA_OBSERVATION_MODE", "screenshot_a11y")
DEFAULT_MODEL_FAMILY = os.getenv(
    "QITOS_MODEL_FAMILY", resolve_family_preset(MODEL_NAME).id
)
MAX_STEPS = 8


class _SequenceModel:
    """Deterministic callable model that returns one scripted output per call."""

    def __init__(
        self,
        outputs: Iterable[str | Callable[[list[dict[str, Any]]], str]],
        *,
        model: str = "smoke-model",
    ):
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, Any]]] = []
        self.model = model

    def __call__(self, messages: list[dict[str, Any]], **_: Any) -> str:
        self.calls.append(list(messages))
        if not self.outputs:
            return "Final Answer: smoke complete"
        item = self.outputs.pop(0)
        return item(messages) if callable(item) else str(item)

    def supports_multimodal_input(self) -> bool:
        return True


def _write_tiny_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wn2gbcAAAAASUVORK5CYII="
    )
    path.write_bytes(payload)


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _multimodal_from_observation(observation: dict[str, Any]) -> dict[str, Any]:
    env = observation.get("env") if isinstance(observation, dict) else {}
    if not isinstance(env, dict):
        return {}
    env_observation = env.get("observation")
    if not isinstance(env_observation, dict):
        return {}
    data = env_observation.get("data")
    if not isinstance(data, dict):
        return {}
    multimodal = data.get("multimodal")
    return dict(multimodal or {}) if isinstance(multimodal, dict) else {}


def _describe_observation(pack: dict[str, Any]) -> tuple[str, list[str], str]:
    ui_candidates = list(pack.get("ui_candidates") or [])
    ocr = list(pack.get("ocr") or [])
    a11y = pack.get("accessibility_tree")
    dom = pack.get("dom")
    title = ""
    if isinstance(dom, dict):
        title = _safe_str(dom.get("title"))
    target_labels = [
        _safe_str(item.get("label") or item.get("name"))
        for item in ui_candidates
        if isinstance(item, dict) and _safe_str(item.get("label") or item.get("name"))
    ]
    if not target_labels:
        target_labels = [
            _safe_str(item.get("text"))
            for item in ocr[:4]
            if isinstance(item, dict) and _safe_str(item.get("text"))
        ]
    visible_summary = []
    if title:
        visible_summary.append(f"DOM title: {title}")
    if target_labels:
        visible_summary.append("UI candidates: " + ", ".join(target_labels[:4]))
    if isinstance(a11y, dict) and _safe_str(a11y.get("name")):
        visible_summary.append("A11y root: " + _safe_str(a11y.get("name")))
    if not visible_summary:
        visible_summary.append("No strong grounding evidence yet.")
    grounding_quality = "strong" if target_labels else "weak"
    return "\n".join(visible_summary), target_labels[:6], grounding_quality


def _extract_action_label(decision: Decision[Action]) -> str:
    if not decision.actions:
        return ""
    return format_action(decision.actions[0])


class DesktopGroundingCritic(Critic):
    """Reject obviously ungrounded or repeated GUI actions before execution."""

    def evaluate(
        self, state: Any, decision: Decision[Any], results: list[Any]
    ) -> dict[str, Any]:
        _ = results
        if not decision.actions:
            return {"action": "continue", "reason": "no_action"}
        action = decision.actions[0]
        name = _safe_str(
            getattr(action, "tool", None)
            or getattr(action, "name", None)
            or (action.get("name") if isinstance(action, dict) else "")
        )
        args = (
            dict(getattr(action, "args", {}) or {})
            if hasattr(action, "args")
            else (dict(action.get("args") or {}) if isinstance(action, dict) else {})
        )
        click_like = {"click", "double_click", "right_click", "move_to", "drag_to"}
        if name in click_like and ("x" not in args or "y" not in args):
            return {
                "action": "retry",
                "reason": "Pointer actions require grounded x/y coordinates.",
                "score": 0.1,
                "details": {"failure_tag": "grounding_failure"},
            }
        if getattr(state, "last_grounding_quality", "weak") == "weak" and name in click_like:
            return {
                "action": "retry",
                "reason": "Grounding evidence is weak. Re-anchor on visible UI candidates or OCR before clicking.",
                "score": 0.2,
                "details": {"failure_tag": "grounding_failure"},
            }
        recent = list(getattr(state, "trajectory", []) or [])
        if recent and len(recent) >= 2:
            last_actions = [x for x in recent[-4:] if str(x).startswith("Action: ")]
            current = "Action: " + _extract_action_label(decision)
            if len(last_actions) >= 2 and all(item == current for item in last_actions[-2:]):
                return {
                    "action": "retry",
                    "reason": "Do not repeat the same desktop action without new evidence.",
                    "score": 0.15,
                    "details": {"failure_tag": "action_selection_failure"},
                }
        return {"action": "continue", "reason": "grounded"}


def build_desktop_critics() -> list[Critic]:
    return [DesktopGroundingCritic()]


@dataclass
class OpenAICUAState(StateSchema):
    observation_mode: str = DEFAULT_OBSERVATION_MODE
    trajectory: list[str] = field(default_factory=list)
    planner_notes: list[str] = field(default_factory=list)
    grounding_notes: list[str] = field(default_factory=list)
    focused_targets: list[str] = field(default_factory=list)
    critic_reasons: list[str] = field(default_factory=list)
    critic_retry_count: int = 0
    last_grounding_quality: str = "weak"
    last_observation_digest: str = ""
    failure_tags: list[str] = field(default_factory=list)


class OpenAICUAAgent(AgentModule[OpenAICUAState, dict[str, Any], Action]):
    name = "openai_cua"

    def __init__(self, llm: Any, *, protocol: str):
        super().__init__(
            llm=llm,
            toolset=[ComputerUseToolSet()],
            model_protocol=protocol,
        )

    def init_state(self, task: str, **kwargs: Any) -> OpenAICUAState:
        return OpenAICUAState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            observation_mode=str(
                kwargs.get("observation_mode", DEFAULT_OBSERVATION_MODE)
            ),
        )

    def base_persona_prompt(self, state: OpenAICUAState) -> str:
        return computer_use_persona_prompt(state.observation_mode)

    def task_policy_prompt(self, state: OpenAICUAState) -> str:
        return computer_use_task_policy(state.observation_mode)

    def extra_instructions_prompt(self, state: OpenAICUAState) -> str:
        _ = state
        return (
            "Desktop baseline workflow:\n"
            "1. planner: state the next short subgoal for the visible desktop.\n"
            "2. grounding: cite the screenshot, OCR, accessibility tree, or UI candidates that justify the action.\n"
            "3. action selector: choose exactly one next GUI action.\n"
            "4. critic discipline: if grounding is weak, choose wait/fail or gather stronger evidence instead of speculative clicks.\n\n"
            "Output guidance:\n"
            "- Include `plan` and `grounding` fields in your JSON whenever possible.\n"
            "- Every step must be grounded in the visible desktop.\n"
            "- Use `wait` if the UI is unstable or still loading.\n"
            "- Use `fail` with a clear blocker when the environment does not expose enough evidence to proceed."
        )

    def prepare(self, state: OpenAICUAState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Observation mode: {state.observation_mode}",
            f"Protocol: {getattr(self.active_protocol(), 'id', self.active_protocol())}",
            f"Step: {state.current_step}/{state.max_steps}",
            "",
            "Planner board:",
            f"- Current subgoal: {state.planner_notes[-1] if state.planner_notes else 'inspect the current desktop and choose the next grounded subgoal'}",
            f"- Grounding summary: {state.grounding_notes[-1] if state.grounding_notes else 'no grounded target selected yet'}",
            f"- Focused targets: {', '.join(state.focused_targets[-4:]) if state.focused_targets else 'none yet'}",
            f"- Grounding quality: {state.last_grounding_quality}",
        ]
        if state.critic_reasons:
            lines.append(
                f"- Critic retry reminders: {' | '.join(state.critic_reasons[-3:])}"
            )
        if state.last_observation_digest:
            lines.extend(["", "Latest desktop digest:", state.last_observation_digest])
        if state.trajectory:
            lines.append("")
            lines.append("Recent trajectory:")
            lines.extend(state.trajectory[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: OpenAICUAState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> OpenAICUAState:
        multimodal = _multimodal_from_observation(observation)
        digest, targets, grounding_quality = _describe_observation(multimodal)
        state.last_observation_digest = digest
        state.focused_targets = targets
        state.last_grounding_quality = grounding_quality
        if targets:
            state.grounding_notes.append("Grounded targets: " + ", ".join(targets))
        else:
            state.grounding_notes.append(
                "Grounding is weak; rely on WAIT or re-inspection."
            )
            if "grounding_failure" not in state.failure_tags:
                state.failure_tags.append("grounding_failure")
        if decision.rationale:
            state.trajectory.append(f"Thought: {decision.rationale}")
        if decision.actions:
            action_label = _extract_action_label(decision)
            state.trajectory.append(f"Action: {action_label}")
            plan_note = (
                f"Use {action_label} to pursue the visible desktop subgoal."
                if action_label
                else "Choose the next grounded desktop action."
            )
            state.planner_notes.append(plan_note)
        elif decision.final_answer:
            state.trajectory.append(f"Final: {decision.final_answer}")
        action_results = observation.get("action_results", []) if isinstance(observation, dict) else []
        if action_results:
            first = action_results[0]
            state.trajectory.append(f"Observation: {first}")
            for result in action_results:
                status = _safe_str((result or {}).get("status"))
                if status in {"validation_error", "approval_required"} and "execution_environment_failure" not in state.failure_tags:
                    state.failure_tags.append("execution_environment_failure")
        state.trajectory = state.trajectory[-50:]
        state.planner_notes = state.planner_notes[-12:]
        state.grounding_notes = state.grounding_notes[-12:]
        return state


def _smoke_outputs() -> list[str]:
    return [
        '{"thought":"The visible desktop shows a clear Continue CTA.","plan":"Click the visible primary button to advance the starter workflow.","grounding":"OCR and UI candidates both point to Continue near the center of the window.","action":{"name":"click","args":{"x":640,"y":420}}}',
        '{"thought":"The grounded click completed the workflow objective.","plan":"Stop and summarize the completed workflow.","grounding":"The task required one precise desktop interaction and that interaction was performed.","final_answer":"Clicked Continue and completed the desktop starter workflow."}',
    ]


def build_model(
    *,
    smoke: bool = False,
    model_family: str,
    model_name: str,
    base_url: str,
    api_key: str | None,
    protocol: str | None = None,
) -> Any:
    if smoke:
        return _SequenceModel(_smoke_outputs(), model=f"smoke-{model_name}")
    if not api_key:
        raise ValueError("Set OPENAI_API_KEY or QITOS_API_KEY before running this example.")
    return build_model_for_preset(
        family_id=model_family,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        protocol=protocol,
        temperature=0.1,
        max_tokens=1600,
    )


def configure_runtime_for_task(
    *,
    task: Task | None = None,
    run_spec: RunSpec | None = None,
    smoke: bool = False,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    run_spec = RunSpec.from_value(run_spec)
    arg_model_name = _safe_str(getattr(args, "model_name", None))
    arg_family = _safe_str(getattr(args, "model_family", None))
    model_name = arg_model_name or _safe_str(run_spec.model_name) or MODEL_NAME
    model_family = arg_family or _safe_str(run_spec.model_family) or DEFAULT_MODEL_FAMILY
    base_url = (
        _safe_str(getattr(args, "base_url", None))
        or _safe_str((run_spec.environment or {}).get("base_url"))
        or MODEL_BASE_URL
    )
    api_key = (
        _safe_str(getattr(args, "api_key", None))
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("QITOS_API_KEY")
        or ""
    )
    protocol_override = (
        _safe_str(getattr(args, "protocol", None))
        or _safe_str(run_spec.prompt_protocol)
        or DEFAULT_PROTOCOL
    )
    harness = build_harness_policy(
        model_name=model_name,
        family_id=model_family,
        protocol=protocol_override or None,
        resolution_source="desktop_osworld_starter_recipe",
    )
    return {
        "smoke": bool(smoke),
        "model_name": model_name,
        "model_family": model_family,
        "base_url": base_url,
        "api_key": api_key,
        "protocol": harness.protocol.id,
        "harness": harness,
        "task": task,
    }


def build_task(
    screenshot_path: Path,
    *,
    smoke: bool = False,
    observation_mode: str = DEFAULT_OBSERVATION_MODE,
) -> Task:
    container = str(os.getenv("QITOS_DESKTOP_CONTAINER", "")).strip()
    provider = "container" if (container and not smoke) else "mock"
    metadata = {
        "lane": "computer_use",
        "observation_mode": observation_mode,
        "provider": provider,
    }
    env_config: dict[str, Any] = {
        "provider": provider,
        "screenshot_path": str(screenshot_path),
        "instruction": TASK_TEXT,
        "accessibility_tree": {
            "role": "window",
            "name": "Desktop Smoke",
            "children": [
                {
                    "role": "button",
                    "name": "Continue",
                    "bounds": [540, 390, 740, 450],
                }
            ],
        },
        "terminal": "$ echo desktop-smoke\ndesktop-smoke\n$ ",
        "dom": {"title": "Desktop Smoke", "buttons": ["Continue"]},
        "ocr": [{"text": "Continue", "x": 610, "y": 420}],
        "ui_candidates": [
            {
                "label": "Continue",
                "role": "button",
                "x": 640,
                "y": 420,
                "confidence": 0.98,
            }
        ],
        "screen_size": [1280, 900],
        "metadata": metadata,
    }
    if provider == "container":
        env_config["container"] = container
        env_config["workspace_root"] = os.getenv("QITOS_DESKTOP_WORKSPACE", "/workspace")

    return Task(
        id="openai_cua_task",
        objective=TASK_TEXT,
        env_spec=EnvSpec(
            type="desktop",
            config=env_config,
            capabilities=["gui_observer", "gui_controller"],
        ),
        budget=TaskBudget(max_steps=MAX_STEPS),
        metadata=metadata,
    )


def build_agent(
    *,
    smoke: bool = False,
    protocol: str = DEFAULT_PROTOCOL,
    model_name: str = MODEL_NAME,
    model_family: str = DEFAULT_MODEL_FAMILY,
    base_url: str = MODEL_BASE_URL,
    api_key: str | None = None,
) -> OpenAICUAAgent:
    llm = build_model(
        smoke=smoke,
        model_family=model_family,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        protocol=protocol,
    )
    return OpenAICUAAgent(llm=llm, protocol=protocol)


@dataclass
class DesktopBaselineExecution:
    agent: OpenAICUAAgent
    task: Task
    run_spec: RunSpec
    experiment_spec: ExperimentSpec | None
    runtime: dict[str, Any]
    harness: Any
    workspace: Path
    result: EngineResult[OpenAICUAState]
    elapsed_seconds: float


def execute_desktop_task(
    *,
    task: Task,
    run_spec: RunSpec | None = None,
    experiment_spec: ExperimentSpec | None = None,
    workspace: str | Path | None = None,
    smoke: bool = False,
    render: bool = False,
    trace: bool = True,
    trace_logdir: str | None = None,
    max_steps: int | None = None,
) -> DesktopBaselineExecution:
    runtime = configure_runtime_for_task(task=task, run_spec=run_spec, smoke=smoke)
    harness = runtime["harness"]
    target_workspace = Path(
        workspace or (run_spec.environment or {}).get("workspace") or WORKSPACE
    ).resolve()
    target_workspace.mkdir(parents=True, exist_ok=True)
    effective_spec = RunSpec.from_value(run_spec)
    effective_spec.model_name = str(runtime["model_name"])
    effective_spec.model_family = str(runtime["model_family"])
    effective_spec.prompt_protocol = harness.protocol.id
    effective_spec.parser_name = harness.parser_name
    effective_spec.toolset_name = "computer_use_tools"
    effective_spec.environment = {
        **dict(effective_spec.environment or {}),
        "base_url": str(runtime["base_url"]),
        "workspace": str(target_workspace),
        "lane": "desktop",
    }
    effective_spec.metadata = {
        **dict(effective_spec.metadata or {}),
        "family_preset": harness.family_preset.id,
        "harness_policy": harness.to_dict(),
        "tool_policy": harness.tool_policy.to_dict(),
        "context_policy": harness.context_policy.to_dict(),
        "baseline_shape": ["planner", "grounding", "action_selector", "critic"],
        "recipe": "desktop_osworld_starter",
    }
    agent = build_agent(
        smoke=runtime["smoke"],
        protocol=str(runtime["protocol"]),
        model_name=str(runtime["model_name"]),
        model_family=str(runtime["model_family"]),
        base_url=str(runtime["base_url"]),
        api_key=str(runtime["api_key"]) if runtime["api_key"] else None,
    )
    started = time.monotonic()
    result = agent.run(
        task=task,
        workspace=str(target_workspace),
        observation_mode=DEFAULT_OBSERVATION_MODE,
        max_steps=int(max_steps or getattr(task.budget, "max_steps", MAX_STEPS) or MAX_STEPS),
        render=render,
        trace=trace,
        trace_logdir=trace_logdir,
        critics=build_desktop_critics(),
        return_state=True,
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
    )
    elapsed = time.monotonic() - started
    return DesktopBaselineExecution(
        agent=agent,
        task=task,
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
        runtime=runtime,
        harness=harness,
        workspace=target_workspace,
        result=result,
        elapsed_seconds=elapsed,
    )


def build_benchmark_result(
    execution: DesktopBaselineExecution,
    *,
    benchmark_name: str = "desktop-starter",
) -> BenchmarkRunResult:
    state = execution.result.state
    task_result = execution.result.task_result
    trace_run_dir = None
    if task_result and isinstance(getattr(task_result, "metadata", None), dict):
        trace_run_dir = task_result.metadata.get("trace_run_dir")
    success = bool(
        getattr(state, "stop_reason", None) == "final"
        and getattr(state, "final_result", None)
    )
    contract = dict((execution.task.metadata or {}).get("completion_contract") or {})
    final_text = str(getattr(state, "final_result", "") or "")
    expected = [
        str(x)
        for x in (contract.get("expects_substrings") or [])
        if str(x).strip()
    ]
    if expected:
        success = all(token.lower() in final_text.lower() for token in expected)
    records = list(getattr(execution.result, "records", []) or [])
    action_count = sum(len(getattr(record, "actions", []) or []) for record in records)
    critic_count = sum(
        len(getattr(record, "critic_outputs", []) or []) for record in records
    )
    failure_tags: list[str] = []
    for tag in list(getattr(state, "failure_tags", []) or []):
        token = str(tag or "").strip()
        if token and token not in failure_tags:
            failure_tags.append(token)
    for record in records:
        diagnostics = getattr(record, "parser_diagnostics", {}) or {}
        if diagnostics and "planning_failure" not in failure_tags:
            failure_tags.append("planning_failure")
        for result in list(getattr(record, "action_results", []) or []):
            tool_result = ToolResult.from_value(result)
            status = str(tool_result.status or "")
            category = str(
                (tool_result.metadata or {}).get("error_category") or ""
            ).strip()
            if (
                category in {"validation_error", "permission_ask", "permission_denied"}
                and "execution_environment_failure" not in failure_tags
            ):
                failure_tags.append("execution_environment_failure")
            if status == "error" and "action_selection_failure" not in failure_tags:
                failure_tags.append("action_selection_failure")
    return BenchmarkRunResult(
        task_id=str(execution.task.id),
        benchmark=benchmark_name,
        split=str(
            (execution.experiment_spec.benchmark_split if execution.experiment_spec else None)
            or execution.run_spec.benchmark_split
            or "starter"
        ),
        prediction=final_text,
        success=success,
        stop_reason=str(getattr(state, "stop_reason", None) or "unknown"),
        steps=int(getattr(execution.result, "step_count", 0) or 0),
        latency_seconds=float(
            ((task_result.metrics or {}).get("elapsed_seconds") if task_result else execution.elapsed_seconds)
            or execution.elapsed_seconds
        ),
        token_usage=int(
            ((task_result.metrics or {}).get("token_usage") if task_result else 0) or 0
        ),
        cost=float(((task_result.metrics or {}).get("cost") if task_result else 0.0) or 0.0),
        trace_run_dir=str(trace_run_dir) if trace_run_dir else None,
        run_spec_ref=execution.run_spec.fingerprint(),
        metadata={
            "task_instruction": execution.task.objective,
            "completion_contract": contract,
            "action_count": action_count,
            "critic_count": critic_count,
            "failure_tags": failure_tags,
            "grounding_failure_tags": [
                tag for tag in failure_tags if tag == "grounding_failure"
            ],
            "parser_failure_tags": [
                tag for tag in failure_tags if tag == "planning_failure"
            ],
            "recipe": "desktop_osworld_starter",
            "family_preset": execution.harness.family_preset.id,
        },
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="QitOS desktop / computer-use starter baseline"
    )
    parser.add_argument("--model-family")
    parser.add_argument("--model-name")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--protocol")
    parser.add_argument("--workspace", default=str(WORKSPACE))
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    parser.add_argument("--print-harness", action="store_true")
    return parser


def main(smoke: bool = False, argv: list[str] | None = None) -> None:
    args = _build_arg_parser().parse_args(argv or [])
    workspace = Path(str(args.workspace)).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    screenshot_path = workspace / SCREENSHOT_FILE
    if smoke or not screenshot_path.exists():
        _write_tiny_png(screenshot_path)

    observation_mode = DEFAULT_OBSERVATION_MODE
    task = build_task(screenshot_path, smoke=smoke, observation_mode=observation_mode)
    runtime = configure_runtime_for_task(task=task, smoke=smoke, args=args)
    if args.print_harness:
        harness = runtime["harness"]
        print("family_preset:", harness.family_preset.id)
        print("model_name:", runtime["model_name"])
        print("base_url:", runtime["base_url"])
        print("protocol:", harness.protocol.id)
        print("parser:", harness.parser_name)
        print("tool_delivery:", harness.tool_policy.primary_delivery)
        print(
            "native_tool_call_preferred:",
            harness.tool_policy.native_tool_call_preferred,
        )
    execution = execute_desktop_task(
        task=task,
        run_spec=RunSpec.infer(
            model_name=str(runtime["model_name"]),
            prompt_protocol=str(runtime["protocol"]),
            parser_name=runtime["harness"].parser_name,
            benchmark_name="desktop-starter",
            benchmark_split="starter",
            environment={
                "base_url": str(runtime["base_url"]),
                "workspace": str(workspace),
                "lane": "desktop",
            },
        ),
        workspace=workspace,
        smoke=smoke,
        render=not smoke,
        trace=not smoke,
        max_steps=int(args.max_steps),
    )
    print("workspace:", execution.workspace)
    print("protocol:", execution.harness.protocol.id)
    print("observation_mode:", observation_mode)
    print("final_result:", execution.result.state.final_result)
    print("stop_reason:", execution.result.state.stop_reason)


if __name__ == "__main__":
    main()
