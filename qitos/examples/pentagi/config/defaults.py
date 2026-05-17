"""PentAGI configuration defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PentAGIConfig:
    """Configuration for a PentAGI penetration test run.

    Attributes
    ----------
    model_provider : str
        LLM provider name (e.g. "openai", "openai-compatible").
    model_name : str
        LLM model name (e.g. "gpt-4o", "qwen-plus").
    api_key : str | None
        API key for the LLM provider.
    base_url : str | None
        Custom API base URL.
    docker_profile : str
        Docker image profile: "kali", "parrot", or "ubuntu".
    docker_image : str | None
        Override Docker image (takes precedence over profile).
    max_subtasks : int
        Maximum number of subtasks to generate.
    max_steps_per_subtask : int
        Maximum Engine steps per subtask execution.
    max_total_steps : int
        Maximum total Engine steps for the entire run.
    language : str
        Response language (e.g. "en", "zh").
    search_backend : str
        Default search backend: "duckduckgo", "searxng", "tavily", "google_cse".
    searxng_url : str | None
        SearXNG instance URL (required if search_backend="searxng").
    pgvector_connection : str | None
        PostgreSQL connection string for pgvector memory.
    graphiti_enabled : bool
        Whether Graphiti knowledge graph is available.
    cache_enabled : bool
        Whether to enable LLM response caching.
    cache_dir : str
        Directory for disk cache.
    workspace : str
        Local workspace directory for output files.
    authorized_targets : List[str]
        List of authorized target IPs/domains.
    container_ports : str
        Exposed container ports (e.g. "80,443,8080").
    ask_user_enabled : bool
        Whether the agent can ask the user for clarification.
    scraper_url : str | None
        URL of the public scraper microservice.
    scraper_private_url : str | None
        URL of the private scraper microservice (for internal network targets).
    summarize_context : bool
        Whether to summarize execution context via LLM before injection.
    temperature : float
        LLM temperature.
    max_tokens : int
        LLM max output tokens.
    context_window : int | None
        Total model context window in tokens. Auto-inferred if None.
    """

    model_provider: str = "openai-compatible"
    model_name: str = "qwen-plus"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    docker_profile: str = "kali"
    docker_image: Optional[str] = None
    max_subtasks: int = 15
    max_steps_per_subtask: int = 15
    max_total_steps: int = 60
    language: str = "en"
    search_backend: str = "duckduckgo"
    searxng_url: Optional[str] = None
    pgvector_connection: Optional[str] = None
    graphiti_enabled: bool = False
    cache_enabled: bool = True
    cache_dir: str = "./runs/cache"
    workspace: str = "./workspace"
    authorized_targets: List[str] = field(default_factory=list)
    container_ports: str = ""
    ask_user_enabled: bool = False
    scraper_url: Optional[str] = None
    scraper_private_url: Optional[str] = None
    summarize_context: bool = True
    mentor_enabled: bool = True
    mentor_interval: int = 5
    planner_enabled: bool = True
    temperature: float = 0.7
    max_tokens: int = 4096
    context_window: Optional[int] = None
