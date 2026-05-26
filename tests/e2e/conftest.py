"""E2E test configuration — real LLM endpoint support.

Set environment variables to enable E2E tests:
  QITOS_E2E_ENDPOINT  — OpenAI-compatible base_url
  QITOS_E2E_API_KEY   — API key
  QITOS_E2E_MODEL     — Model name (default: gpt-4o-mini)

Tests are skipped if these variables are not set.
"""
from __future__ import annotations

import os

import pytest


def _e2e_available() -> bool:
    return bool(os.environ.get("QITOS_E2E_ENDPOINT") and os.environ.get("QITOS_E2E_API_KEY"))


def get_e2e_endpoint() -> str:
    return os.environ.get("QITOS_E2E_ENDPOINT", "")


def get_e2e_api_key() -> str:
    return os.environ.get("QITOS_E2E_API_KEY", "")


def get_e2e_model() -> str:
    return os.environ.get("QITOS_E2E_MODEL", "gpt-4o-mini")


def create_e2e_llm(**overrides):
    """Create an LLM instance for E2E testing.

    Returns an OpenAI-compatible model adapter using the configured endpoint.
    """
    from qitos.models.openai import OpenAICompatibleModel

    return OpenAICompatibleModel(
        model=get_e2e_model(),
        api_key=get_e2e_api_key(),
        base_url=get_e2e_endpoint(),
        **overrides,
    )


def create_e2e_engine(agent, **overrides):
    """Create an Engine for E2E testing."""
    from qitos.engine.engine import Engine

    return Engine(agent=agent, **overrides)


def create_e2e_engine_with_registry(agent, specs, **overrides):
    """Create an Engine with an AgentRegistry populated from specs.

    Parameters
    ----------
    agent : AgentModule
        The primary agent.
    specs : list[AgentSpec]
        Agent specs to register.
    **overrides
        Additional Engine constructor parameters.
    """
    from qitos.core.agent_spec import AgentRegistry, AgentSpec
    from qitos.engine.engine import Engine

    registry = AgentRegistry()
    for spec in specs:
        registry.register(spec)
    return Engine(agent=agent, agent_registry=registry, **overrides)


# Skip all E2E tests if endpoint is not configured
e2e_skip = pytest.mark.skipif(
    not _e2e_available(),
    reason="E2E tests require QITOS_E2E_ENDPOINT and QITOS_E2E_API_KEY environment variables",
)

e2e_marker = pytest.mark.e2e

# Mark for inherently non-deterministic E2E tests (stagnation, recovery)
e2e_flaky = pytest.mark.e2e
