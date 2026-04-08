"""Private environment helpers for Engine."""

from __future__ import annotations

import time
from typing import Any, Dict, Generic, List, Optional, TypeVar

from ..core.decision import Decision
from ..core.env import Env, EnvObservation, EnvSpec, EnvStepResult
from ..core.state import StateSchema
from ..core.task import Task
from .states import RuntimePhase


StateT = TypeVar("StateT", bound=StateSchema)
ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")


class _EnvRuntime(Generic[StateT, ObservationT, ActionT]):
    def __init__(self, engine: Any):
        self.engine = engine

    def build_env_view(
        self, state: StateT, step_id: int, started_at: float
    ) -> Dict[str, Any]:
        engine = self.engine
        elapsed = time.monotonic() - started_at
        env_payload = self.env_payload()
        return {
            "step_id": step_id,
            "elapsed_seconds": elapsed,
            "budget": {
                "max_steps": engine.budget.max_steps,
                "max_runtime_seconds": engine.budget.max_runtime_seconds,
                "max_tokens": engine.budget.max_tokens,
                "consumed_tokens": engine._token_usage,
            },
            "metadata": state.metadata,
            "env": env_payload,
            "task": (
                engine._active_task_obj.to_dict()
                if engine._active_task_obj is not None
                else {"objective": engine._active_task}
            ),
        }

    def build_initial_observation(
        self, state: StateT, step_id: int, started_at: float
    ) -> ObservationT:
        env_view = self.build_env_view(state, step_id, started_at)
        obs = {
            "task": self.engine._active_task,
            "step": step_id,
            "state": state.to_dict(),
            "env": env_view.get("env", {}),
            "action_results": [],
        }
        return obs  # type: ignore[return-value]

    def build_observation_after_action(
        self,
        state: StateT,
        step_id: int,
        started_at: float,
        decision: Decision[ActionT],
        action_results: List[Any],
    ) -> ObservationT:
        env_view = self.build_env_view(state, step_id, started_at)
        obs = {
            "task": self.engine._active_task,
            "step": step_id,
            "state": state.to_dict(),
            "decision": (
                decision.to_dict() if hasattr(decision, "to_dict") else decision
            ),
            "action_results": list(action_results),
            "env": env_view.get("env", {}),
        }
        self.engine._emit(
            step_id,
            RuntimePhase.ACT,
            payload={"stage": "observation_ready", "observation": obs},
        )
        return obs  # type: ignore[return-value]

    def validate_env_capabilities(self) -> List[Dict[str, Any]]:
        required = self.collect_required_ops()
        engine = self.engine
        if not required:
            return []
        if engine.env is None:
            return [
                {
                    "code": "ENV_REQUIRED_OPS_MISSING",
                    "message": "No env configured but tools require env ops",
                    "field": "env",
                    "details": {"required_ops": sorted(required)},
                }
            ]
        missing = [group for group in sorted(required) if not engine.env.has_ops(group)]
        if not missing:
            return []
        return [
            {
                "code": "ENV_OPS_GROUP_MISSING",
                "message": "Env is missing required ops groups",
                "field": "env",
                "details": {
                    "env_name": getattr(
                        engine.env, "name", engine.env.__class__.__name__
                    ),
                    "missing_ops": missing,
                    "required_ops": sorted(required),
                },
            }
        ]

    def collect_required_ops(self) -> set[str]:
        engine = self.engine
        required: set[str] = set()
        if engine.tool_registry is None or not hasattr(
            engine.tool_registry, "list_tools"
        ):
            return required
        try:
            for tool_name in engine.tool_registry.list_tools():
                tool = (
                    engine.tool_registry.get(tool_name)
                    if hasattr(engine.tool_registry, "get")
                    else None
                )
                spec = getattr(tool, "spec", None)
                groups = getattr(spec, "required_ops", None)
                if isinstance(groups, list):
                    required.update(str(x) for x in groups if str(x))
        except Exception:
            return required
        return required

    def validate_env_health(self) -> Optional[Dict[str, Any]]:
        engine = self.engine
        if engine.env is None:
            return None
        try:
            probe = engine.env.health_check()
        except Exception as exc:
            return {
                "code": "ENV_HEALTH_CHECK_EXCEPTION",
                "message": f"Env health_check raised exception: {exc}",
                "field": "env",
                "details": {
                    "env_name": getattr(
                        engine.env, "name", engine.env.__class__.__name__
                    )
                },
            }
        if not isinstance(probe, dict):
            return None
        if bool(probe.get("ok", True)):
            return None
        return {
            "code": "ENV_HEALTH_CHECK_FAILED",
            "message": str(probe.get("message", "Environment health probe failed")),
            "field": "env",
            "details": probe,
        }

    def setup_env(
        self, task_obj: Optional[Task], state: StateT, kwargs: Dict[str, Any]
    ) -> None:
        engine = self.engine
        if (
            engine.env is None
            and task_obj is not None
            and task_obj.env_spec is not None
        ):
            engine.env = self.build_env_from_spec(
                task_obj.env_spec, fallback_workspace=kwargs.get("workspace")
            )
        if engine.env is None:
            return
        workspace = kwargs.get("workspace")
        reset_task: Any = task_obj if task_obj is not None else engine._active_task
        resources = (
            task_obj.resolve_resources(workspace=str(workspace) if workspace else None)
            if task_obj is not None
            else []
        )
        try:
            engine.env.setup(task=reset_task, workspace=workspace, resources=resources)
            first = engine.env.reset(
                task=reset_task, workspace=workspace, resources=resources
            )
            if not isinstance(first, EnvObservation):
                first = EnvObservation(data={"value": first})
            engine._last_env_observation = first
            engine._last_env_result = EnvStepResult(
                observation=first, done=False, info={"source": "reset"}
            )
        except Exception as exc:
            engine._last_env_observation = EnvObservation(data={"error": str(exc)})
            engine._last_env_result = EnvStepResult(
                observation=engine._last_env_observation, done=False, error=str(exc)
            )

    def build_env_from_spec(
        self, env_spec: EnvSpec, fallback_workspace: Any = None
    ) -> Optional[Env]:
        env_type = str(getattr(env_spec, "type", "")).strip().lower()
        config = getattr(env_spec, "config", {})
        if not isinstance(config, dict):
            config = {}
        workspace_root = str(config.get("workspace_root") or fallback_workspace or ".")
        if env_type in {"repo", "repository"}:
            try:
                from ..kit.env import RepoEnv

                return RepoEnv(workspace_root=workspace_root)
            except Exception:
                return None
        if env_type in {"host", "local"}:
            try:
                from ..kit.env import HostEnv

                return HostEnv(workspace_root=workspace_root)
            except Exception:
                return None
        if env_type in {"docker", "container"}:
            try:
                from ..kit.env import DockerEnv

                container = str(config.get("container", "")).strip()
                if not container:
                    return None
                container_workspace = str(
                    config.get("container_workspace") or workspace_root or "/workspace"
                )
                return DockerEnv(
                    container=container, workspace_root=container_workspace
                )
            except Exception:
                return None
        if env_type in {"tmux", "terminal"}:
            try:
                from ..kit.env import TmuxEnv

                session_name = str(config.get("session_name") or "").strip() or None
                auto_kill = bool(config.get("auto_kill", True))
                return TmuxEnv(
                    workspace_root=workspace_root,
                    session_name=session_name,
                    auto_kill=auto_kill,
                )
            except Exception:
                return None
        if env_type in {"screenshot", "gui", "multimodal"}:
            try:
                from ..kit.env import ScreenshotEnv

                screenshot_path = str(config.get("screenshot_path") or "").strip()
                if not screenshot_path:
                    return None
                return ScreenshotEnv(
                    screenshot_path=screenshot_path,
                    text=str(config.get("text") or ""),
                    detail=str(config.get("detail") or "high"),
                    dom=config.get("dom"),
                    accessibility_tree=config.get("accessibility_tree"),
                    ocr=list(config.get("ocr") or []),
                    ui_candidates=list(config.get("ui_candidates") or []),
                    metadata=dict(config.get("metadata") or {}),
                )
            except Exception:
                return None
        if env_type in {"desktop", "computer_use", "gui_desktop"}:
            try:
                from ..kit.env import DesktopEnv

                provider = str(config.get("provider") or "mock").strip().lower()
                screenshot_path = str(config.get("screenshot_path") or "").strip()
                if not screenshot_path:
                    return None
                instruction = str(
                    config.get("instruction")
                    or config.get("task")
                    or config.get("objective")
                    or ""
                )
                accessibility_tree = config.get("accessibility_tree")
                terminal = str(config.get("terminal") or "")
                dom = config.get("dom")
                ocr = list(config.get("ocr") or [])
                ui_candidates = list(config.get("ui_candidates") or [])
                raw_screen_size = config.get("screen_size") or (1920, 1080)
                if (
                    isinstance(raw_screen_size, (list, tuple))
                    and len(raw_screen_size) >= 2
                ):
                    screen_size = (
                        int(raw_screen_size[0]),
                        int(raw_screen_size[1]),
                    )
                else:
                    screen_size = (1920, 1080)
                metadata = dict(config.get("metadata") or {})
                if provider == "container":
                    container = str(config.get("container") or "").strip()
                    if not container:
                        return None
                    return DesktopEnv.from_container(
                        container=container,
                        workspace_root=str(
                            config.get("workspace_root")
                            or config.get("container_workspace")
                            or workspace_root
                            or "/workspace"
                        ),
                        screenshot_path=screenshot_path,
                        instruction=instruction,
                        accessibility_tree=accessibility_tree,
                        terminal=terminal,
                        dom=dom,
                        ocr=ocr,
                        ui_candidates=ui_candidates,
                        screen_size=screen_size,
                        metadata=metadata,
                    )
                return DesktopEnv.from_mock(
                    screenshot_path=screenshot_path,
                    instruction=instruction,
                    accessibility_tree=accessibility_tree,
                    terminal=terminal,
                    dom=dom,
                    ocr=ocr,
                    ui_candidates=ui_candidates,
                    screen_size=screen_size,
                    metadata=metadata,
                )
            except Exception:
                return None
        return None

    def teardown_env(self) -> None:
        engine = self.engine
        if engine.env is None:
            return
        try:
            engine.env.teardown()
        except Exception:
            pass

    def run_env_step(
        self, decision: Decision[ActionT], action_results: List[Any]
    ) -> Optional[EnvStepResult]:
        engine = self.engine
        if engine.env is None:
            return None
        try:
            result = engine.env.step(
                action={
                    "decision_mode": decision.mode,
                    "actions": decision.actions,
                    "final_answer": decision.final_answer,
                    "action_results": action_results,
                },
                state=engine._active_state,
            )
            if not isinstance(result, EnvStepResult):
                result = EnvStepResult(
                    observation=EnvObservation(data={"value": result})
                )
            engine._last_env_result = result
            engine._last_env_observation = result.observation
            engine._emit(
                (
                    engine._active_state.current_step
                    if engine._active_state is not None
                    else 0
                ),
                RuntimePhase.ACT,
                payload={
                    "stage": "env_step",
                    "env_result": self.env_step_result_to_dict(result),
                },
            )
            return result
        except Exception as exc:
            err = EnvStepResult(
                observation=EnvObservation(data={"error": str(exc)}),
                done=False,
                error=str(exc),
            )
            engine._last_env_result = err
            engine._last_env_observation = err.observation
            engine._emit(
                (
                    engine._active_state.current_step
                    if engine._active_state is not None
                    else 0
                ),
                RuntimePhase.ACT,
                ok=False,
                payload={"stage": "env_step_error"},
                error=str(exc),
            )
            return err

    def env_payload(self) -> Dict[str, Any]:
        engine = self.engine
        if engine.env is None:
            return {"enabled": False}
        ident = self.env_identity()
        return {
            "enabled": True,
            "name": ident["name"],
            "version": ident["version"],
            "observation": self.env_observation_to_dict(engine._last_env_observation),
            "last_result": (
                self.env_step_result_to_dict(engine._last_env_result)
                if engine._last_env_result is not None
                else None
            ),
        }

    def env_identity(self) -> Dict[str, Any]:
        engine = self.engine
        if engine.env is None:
            return {"enabled": False, "name": None, "version": None}
        return {
            "enabled": True,
            "name": getattr(engine.env, "name", engine.env.__class__.__name__),
            "version": getattr(engine.env, "version", "0"),
        }

    def env_observation_to_dict(
        self, observation: Optional[EnvObservation]
    ) -> Optional[Dict[str, Any]]:
        if observation is None:
            return None
        return {"data": observation.data, "metadata": observation.metadata}

    def env_step_result_to_dict(
        self, result: Optional[EnvStepResult]
    ) -> Optional[Dict[str, Any]]:
        if result is None:
            return None
        return {
            "observation": self.env_observation_to_dict(result.observation),
            "done": result.done,
            "reward": result.reward,
            "info": result.info,
            "error": result.error,
        }
