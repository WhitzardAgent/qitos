"""Build runtime objects from AgentConfig."""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

from ..core.spec import RunSpec
from ..core.tool_registry import ToolRegistry
from ..models.base import ModelFactory
from .loader import AgentConfig, ModelConfig


# Provider name aliases: YAML-friendly names -> ModelFactory keys
_PROVIDER_ALIASES = {
    "openai_compatible": "openai-compatible",
    "openai-compatible": "openai-compatible",
    "azure": "azure",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
    "litellm": "litellm",
    "ollama": "ollama",
    "lmstudio": "lmstudio",
    "local": "ollama",
}


def build_model(config: ModelConfig) -> Any:
    """Create a Model instance from ModelConfig using ModelFactory.

    Args:
        config: Model configuration from YAML.

    Returns:
        A Model instance.

    Raises:
        ValueError: If the provider is unknown or required fields are missing.
    """
    provider_key = _PROVIDER_ALIASES.get(config.provider, config.provider)
    params: Dict[str, Any] = {}

    # Common params
    if config.model or config.model_name:
        params["model"] = config.model or config.model_name
    if config.api_key:
        params["api_key"] = config.api_key
    if config.base_url:
        params["base_url"] = config.base_url
    params["temperature"] = config.temperature
    params["max_tokens"] = config.max_tokens
    if config.context_window is not None:
        params["context_window"] = config.context_window

    return ModelFactory.create(provider_key, **params)


def build_run_spec(config: AgentConfig) -> RunSpec:
    """Create a RunSpec from AgentConfig.

    Args:
        config: Agent configuration from YAML.

    Returns:
        A RunSpec with fields populated from the config.
    """
    model_name = config.model.model or config.model.model_name or "unknown"
    return RunSpec.infer(
        model_name=model_name,
        prompt_protocol=config.protocol,
        seed=config.seed,
    )


def build_tool_registry(config: AgentConfig) -> ToolRegistry:
    """Resolve dotted tool paths and build a ToolRegistry.

    Tool paths are dotted module paths ending in a function name,
    e.g. ``qitos.kit.search.web_search``.

    Args:
        config: Agent configuration with ``tools`` list.

    Returns:
        A ToolRegistry with all resolved tools registered.
    """
    registry = ToolRegistry()
    for tool_path in config.tools:
        _register_tool_by_path(registry, tool_path)
    return registry


def _register_tool_by_path(registry: ToolRegistry, dotted_path: str) -> None:
    """Import a tool by its dotted path and register it.

    The path format is ``module.path.function_name``.
    The last component is the function name; everything before is the module.
    """
    parts = dotted_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ImportError(
            f"Invalid tool path '{dotted_path}': expected format 'module.function'"
        )
    module_path, func_name = parts
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(f"Cannot import tool module '{module_path}': {e}") from e

    func = getattr(module, func_name, None)
    if func is None:
        raise ImportError(
            f"Tool '{func_name}' not found in module '{module_path}'"
        )
    registry.register(func)


__all__ = ["build_model", "build_run_spec", "build_tool_registry"]
