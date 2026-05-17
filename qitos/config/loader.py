"""YAML agent configuration loader."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class ModelConfig:
    """Model configuration from YAML."""

    provider: str = "openai"
    model: str = ""
    model_name: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    context_window: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "model_name": self.model_name,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "context_window": self.context_window,
        }


@dataclass
class DatasetItem:
    """A single task in the dataset."""

    task: str = ""
    expected: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentConfig:
    """Agent configuration loaded from YAML."""

    name: str = "agent"
    max_steps: int = 10
    model: ModelConfig = field(default_factory=ModelConfig)
    dataset: List[DatasetItem] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    protocol: Optional[str] = None
    parser: Optional[str] = None
    environment: Dict[str, Any] = field(default_factory=dict)
    seed: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "max_steps": self.max_steps,
            "model": self.model.to_dict(),
            "dataset": [
                {"task": d.task, "expected": d.expected, "metadata": d.metadata}
                for d in self.dataset
            ],
            "tools": self.tools,
            "protocol": self.protocol,
            "parser": self.parser,
            "environment": self.environment,
            "seed": self.seed,
            "metadata": self.metadata,
        }


_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def resolve_env_vars(value: Any) -> Any:
    """Replace ${VAR} patterns with environment variable values."""
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars(item) for item in value]
    return value


def load_agent_config(path: str | Path) -> AgentConfig:
    """Load a YAML config file and return an AgentConfig.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        Parsed AgentConfig with environment variables resolved.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config file is malformed.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML mapping, got {type(raw).__name__}")

    raw = resolve_env_vars(raw)
    return _parse_agent_config(raw)


def _parse_agent_config(raw: Dict[str, Any]) -> AgentConfig:
    model_raw = raw.get("model", {})
    if isinstance(model_raw, dict):
        model_config = ModelConfig(
            provider=str(model_raw.get("provider", "openai")),
            model=str(model_raw.get("model", "")),
            model_name=str(model_raw.get("model_name", "")),
            api_key=str(model_raw.get("api_key", "")),
            base_url=str(model_raw.get("base_url", "")),
            temperature=float(model_raw.get("temperature", 0.7)),
            max_tokens=int(model_raw.get("max_tokens", 2048)),
            context_window=model_raw.get("context_window"),
        )
    else:
        model_config = ModelConfig()

    dataset_raw = raw.get("dataset", [])
    dataset = []
    if isinstance(dataset_raw, list):
        for item in dataset_raw:
            if isinstance(item, dict):
                dataset.append(
                    DatasetItem(
                        task=str(item.get("task", "")),
                        expected=item.get("expected"),
                        metadata=item.get("metadata", {}),
                    )
                )
            elif isinstance(item, str):
                dataset.append(DatasetItem(task=item))

    return AgentConfig(
        name=str(raw.get("name", "agent")),
        max_steps=int(raw.get("max_steps", 10)),
        model=model_config,
        dataset=dataset,
        tools=list(raw.get("tools", [])),
        protocol=raw.get("protocol"),
        parser=raw.get("parser"),
        environment=dict(raw.get("environment", {})),
        seed=raw.get("seed"),
        metadata=dict(raw.get("metadata", {})),
    )


__all__ = ["AgentConfig", "ModelConfig", "DatasetItem", "load_agent_config", "resolve_env_vars"]
