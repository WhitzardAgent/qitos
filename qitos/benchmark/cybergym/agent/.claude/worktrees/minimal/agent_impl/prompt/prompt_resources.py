"""Prompt resource loading helpers."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any


@lru_cache(maxsize=None)
def prompt_resource(path: str) -> str:
    return (
        resources.files("cybergym_agent.agent_prompts")
        .joinpath(path)
        .read_text(encoding="utf-8")
    )


def render_prompt_resource(path: str, **replacements: Any) -> str:
    text = prompt_resource(path)
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text
