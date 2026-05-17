"""Thin example entrypoint for the canonical desktop starter recipe."""

from __future__ import annotations

from qitos.recipes.desktop.osworld_starter import (
    DEFAULT_MODEL_FAMILY,
    DEFAULT_OBSERVATION_MODE,
    DEFAULT_PROTOCOL,
    DesktopBaselineExecution,
    DesktopGroundingCritic,
    OpenAICUAAgent,
    OpenAICUAState,
    build_agent,
    build_benchmark_result,
    build_desktop_critics,
    build_model,
    build_task,
    configure_runtime_for_task,
    execute_desktop_task,
    main,
)

__all__ = [
    "DEFAULT_MODEL_FAMILY",
    "DEFAULT_OBSERVATION_MODE",
    "DEFAULT_PROTOCOL",
    "DesktopBaselineExecution",
    "DesktopGroundingCritic",
    "OpenAICUAAgent",
    "OpenAICUAState",
    "build_agent",
    "build_benchmark_result",
    "build_desktop_critics",
    "build_model",
    "build_task",
    "configure_runtime_for_task",
    "execute_desktop_task",
    "main",
]


if __name__ == "__main__":
    main()
