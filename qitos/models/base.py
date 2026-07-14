"""
Model Base Classes

Unified LLM calling interface.

Design Principles:
1. All model implementations are callable objects
2. Input: OpenAI-style messages list
3. Output: Text by default, with optional raw-response access for structured runtimes
"""

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Optional, Type

from ..core.multimodal import content_to_text, normalize_messages
from .context_registry import infer_context_window


@dataclass
class ModelStreamChunk:
    """A single chunk from a streaming model response."""

    text: str
    done: bool = False
    usage: Optional[Dict[str, Any]] = field(default=None)
    tool_calls: Optional[List[Dict[str, Any]]] = field(default=None)

    @property
    def is_final(self) -> bool:
        return self.done


class Model(ABC):
    """
    Unified model calling interface

    All model implementations (OpenAI, Ollama, Anthropic, etc.) should inherit from this class.

    Interface Contract:
    - Input: OpenAI-style messages list
    - Output: Text format that can be parsed by parse_tool_calls()
    - Advanced runtimes may call `call_raw(...)` to preserve provider-native structure

    Output Format Specification:
    ```
    Action: tool_name(arg1="value1", arg2="value2")

    Or

    Action 1: search
    "query": "python tutorial"

    Or

    Final Answer: This is the final answer
    ```

    Example:
        llm = OpenAIModel(model="gpt-4")
        result = llm([{"role": "user", "content": "Help me search"}])
        # Returns: "Action: search(query='python tutorial')\n\n"
    """

    def __init__(
        self,
        model: str = "default",
        system_prompt: Optional[str] = None,
        temperature: float | None = 0.7,
        max_tokens: int = 2048,
        context_window: Optional[int] = None,
    ):
        """
        Initialize model

        Args:
            model: Model name
            system_prompt: System prompt
            temperature: Temperature parameter (0.0-1.0)
            max_tokens: Maximum output token count
            context_window: Total model context window used for input/output budgeting
        """
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.context_window = self._resolve_context_window(context_window)
        self._last_usage: Optional[Dict[str, Any]] = None

    @abstractmethod
    def _call_api(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        """
        Actually call the model API

        Subclasses must implement this method.

        Args:
            messages: OpenAI-style messages list

        Returns:
            Raw model output text
        """
        pass

    def __call__(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        """
        Call model to generate response

        Args:
            messages: OpenAI-style messages list
                [{"role": "system", "content": "..."}, ...]

        Returns:
            Text that can be parsed by parse_tool_calls()
        """
        self._last_usage = None
        return self._call_api(messages, **kwargs)

    def call_raw(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        """
        Advanced runtime entrypoint that may return a provider-native response object.

        The default implementation preserves the historic text-only behavior.
        Concrete adapters can override this to avoid flattening tool calls too early.
        """
        return self(messages, **kwargs)

    def stream(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Iterator[ModelStreamChunk]:
        """
        Stream model response as chunks.

        The default implementation falls back to the non-streaming path,
        yielding the full text as a single final chunk. Concrete adapters
        should override this for real token-level streaming.

        Args:
            messages: OpenAI-style messages list
            **kwargs: Additional model parameters

        Yields:
            ModelStreamChunk objects, with the final chunk having done=True
        """
        self._last_usage = None
        text = self._call_api(messages, **kwargs)
        yield ModelStreamChunk(text=text, done=True, usage=self._last_usage)

    def supports_tool_schema_delivery(
        self, delivery: str, protocol: Any = None
    ) -> bool:
        """Return whether the model adapter supports the requested tool delivery mode."""
        _ = protocol
        return str(delivery or "prompt_injection") == "prompt_injection"

    def build_tool_schema_request_options(
        self,
        tool_schema_payload: Optional[List[Dict[str, Any]]],
        *,
        protocol: Any = None,
        delivery: str = "prompt_injection",
    ) -> Dict[str, Any]:
        """Return model-call kwargs for native tool schema delivery when supported."""
        _ = tool_schema_payload
        _ = protocol
        _ = delivery
        return {}

    def supports_multimodal_input(self) -> bool:
        """Whether this adapter can accept multimodal message content arrays."""
        return False

    def count_tokens(self, messages_or_text: Any) -> Optional[int]:
        """
        Estimate token count for messages or plain text.

        This default implementation is heuristic and provider-agnostic. Concrete
        model adapters may override it with tokenizer-aware counting.
        """
        text = self._stringify_token_payload(messages_or_text)
        if not text:
            return 0
        pieces = re.findall(r"\w+|[^\s\w]", text, flags=re.UNICODE)
        return len(pieces)

    def extract_usage(self, response: Any = None) -> Optional[Dict[str, Any]]:
        """
        Return provider-reported token usage for the most recent call when available.
        """
        _ = response
        if not isinstance(self._last_usage, dict):
            return None
        return dict(self._last_usage)

    def _set_last_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        self._last_usage = dict(usage) if isinstance(usage, dict) else None

    def _resolve_context_window(self, explicit: Optional[int]) -> Optional[int]:
        if isinstance(explicit, int) and explicit > 0:
            return explicit
        inferred = infer_context_window(self.model)
        if isinstance(inferred, int) and inferred > 0:
            return inferred
        return 128000

    def _stringify_token_payload(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            parts: List[str] = []
            for item in payload:
                if isinstance(item, dict):
                    role = item.get("role", "")
                    content = content_to_text(item.get("content"))
                    parts.append(f"{role}: {content}")
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        if isinstance(payload, dict):
            return "\n".join(f"{k}: {v}" for k, v in payload.items())
        return str(payload)

    def format_messages(
        self, user_content: str, history: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Format messages (helper method)

        Args:
            user_content: User input
            history: Historical messages (observations, etc.)

        Returns:
            Formatted messages list
        """
        messages: List[Dict[str, Any]] = []

        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        if history:
            messages.extend(normalize_messages(history))
        messages.append({"role": "user", "content": user_content})

        return messages

    def format_tool_response(
        self, tool_name: str, args: Dict[str, Any], result: Any
    ) -> str:
        """
        Format tool response (for multi-turn dialogue)

        Args:
            tool_name: Tool name
            args: Tool parameters
            result: Tool execution result

        Returns:
            Formatted observation result
        """
        return f"""Observed result from {tool_name}:
{result}

Please decide on the next action, or provide a Final Answer if the task is complete."""

    def format_final_answer(self, answer: str) -> str:
        """
        Format final answer

        Args:
            answer: Final answer

        Returns:
            Final answer in parse_tool_calls compatible format
        """
        return f"Final Answer: {answer}"

    def format_action(self, tool_name: str, args: Dict[str, Any]) -> str:
        """
        Format tool call

        Args:
            tool_name: Tool name
            args: Tool parameters

        Returns:
            Tool call in parse_tool_calls compatible format
        """
        args_str = ", ".join(
            f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}" for k, v in args.items()
        )
        return f"Action: {tool_name}({args_str})"

    @property
    def config(self) -> Dict[str, Any]:
        """
        Get model configuration (for debugging)
        """
        return {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "context_window": self.context_window,
            "system_prompt": self.system_prompt,
        }

    def __repr__(self) -> str:
        return f"Model(model='{self.model}', temperature={self.temperature})"


class AsyncModel(Model):
    """
    Async model base class

    Supports async API calls (e.g., aiohttp, httpx)
    """

    @abstractmethod
    async def _acall_api(self, messages: List[Dict[str, Any]]) -> str:
        """
        Async call to model API

        Subclasses must implement this method.
        """
        pass

    def _call_api(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        import asyncio

        _ = kwargs
        return asyncio.run(self._acall_api(messages))

    async def acall(self, messages: List[Dict[str, Any]]) -> str:
        """Async call to model."""
        return await self._acall_api(messages)

    async def astream(self, messages: List[Dict[str, Any]], **kwargs: Any) -> AsyncIterator[ModelStreamChunk]:
        """
        Async stream model response as chunks.

        The default implementation falls back to the non-streaming path,
        yielding the full text as a single final chunk. Concrete adapters
        should override this for real token-level streaming.

        Args:
            messages: OpenAI-style messages list
            **kwargs: Additional model parameters

        Yields:
            ModelStreamChunk objects, with the final chunk having done=True
        """
        self._last_usage = None
        text = await self._acall_api(messages)
        yield ModelStreamChunk(text=text, done=True, usage=self._last_usage)


class ModelFactory:
    """
    Model factory

    Create different types of models based on configuration
    """

    _providers: Dict[str, Type[Model]] = {}

    @classmethod
    def register(cls, name: str) -> Callable:
        """Register model provider"""

        def decorator(model_class):
            cls._providers[name] = model_class
            return model_class

        return decorator

    @classmethod
    def create(cls, provider: str, **kwargs) -> Model:
        """
        Create model instance

        Args:
            provider: Provider identifier ("openai", "ollama", "local", etc.)
            **kwargs: Model configuration parameters

        Returns:
            Model instance

        Raises:
            ValueError: Unsupported provider
        """
        if provider not in cls._providers:
            raise ValueError(f"Unknown model provider: {provider}")

        return cls._providers[provider](**kwargs)

    @classmethod
    def from_env(cls, **kwargs) -> Optional[Model]:
        """
        Create model from environment variables

        Check environment variables and automatically select appropriate model

        Returns:
            Model instance, or None if unable to create
        """
        provider = os.getenv("QITOS_MODEL_PROVIDER") or os.getenv("MODEL_PROVIDER")
        if provider:
            provider_name = str(provider).strip().lower()
            params = dict(kwargs)
            if provider_name == "anthropic":
                params.setdefault("api_key", os.getenv("ANTHROPIC_API_KEY"))
            elif provider_name in {"gemini", "google"}:
                params.setdefault(
                    "api_key",
                    os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
                )
            elif provider_name == "litellm":
                params.setdefault(
                    "model",
                    os.getenv("LITELLM_MODEL", params.get("model", "gpt-4o-mini")),
                )
                params.setdefault("api_key", os.getenv("LITELLM_API_KEY"))
                params.setdefault("api_base", os.getenv("LITELLM_API_BASE"))
                params.setdefault("api_version", os.getenv("LITELLM_API_VERSION"))
                params.setdefault("custom_llm_provider", os.getenv("LITELLM_PROVIDER"))
            elif provider_name == "ollama":
                params.setdefault(
                    "host", os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST")
                )
            elif provider_name == "lmstudio":
                params.setdefault("base_url", os.getenv("LM_STUDIO_BASE_URL"))
            return cls.create(provider_name, **params)

        # Check OpenAI
        if os.getenv("OPENAI_API_KEY"):
            return cls.create("openai", **kwargs)

        if os.getenv("ANTHROPIC_API_KEY"):
            return cls.create("anthropic", **kwargs)

        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            return cls.create("gemini", **kwargs)

        if os.getenv("LITELLM_MODEL"):
            params = dict(kwargs)
            params.setdefault("model", os.getenv("LITELLM_MODEL", "gpt-4o-mini"))
            params.setdefault("api_key", os.getenv("LITELLM_API_KEY"))
            params.setdefault("api_base", os.getenv("LITELLM_API_BASE"))
            params.setdefault("api_version", os.getenv("LITELLM_API_VERSION"))
            params.setdefault("custom_llm_provider", os.getenv("LITELLM_PROVIDER"))
            return cls.create("litellm", **params)

        # Check Ollama
        if os.getenv("OLLAMA_HOST") or os.getenv("OLLAMA_BASE_URL"):
            return cls.create("ollama", **kwargs)

        if os.getenv("LM_STUDIO_BASE_URL"):
            return cls.create("lmstudio", **kwargs)

        return None
