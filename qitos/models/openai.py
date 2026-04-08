"""
OpenAI Model Implementation

OpenAI API-based model calling implementation.
Supports environment variable configuration: OPENAI_API_KEY, OPENAI_BASE_URL
"""

import json
import os
from typing import Any, Dict, List, Optional, cast

from ..core.multimodal import (
    ensure_data_url,
    file_to_data_url,
    has_nontext_content,
    normalize_content_block,
    normalize_messages,
)
from .base import Model


def _to_openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = normalize_messages(messages)
    out: List[Dict[str, Any]] = []
    for message in normalized:
        role = str(message.get("role") or "user").strip() or "user"
        content = message.get("content")
        payload: Dict[str, Any] = {"role": role}
        for key, value in message.items():
            if key in {"role", "content"}:
                continue
            payload[key] = value
        if isinstance(content, list):
            if has_nontext_content(message):
                payload["content"] = _to_openai_content_blocks(content)
            else:
                text_blocks = [
                    str(normalize_content_block(block).get("text") or "")
                    for block in content
                    if str(normalize_content_block(block).get("type") or "text")
                    == "text"
                ]
                payload["content"] = "\n".join(part for part in text_blocks if part)
        else:
            payload["content"] = str(content or "")
        out.append(payload)
    return out


def _to_openai_content_blocks(content: List[Any]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for raw in content:
        block = normalize_content_block(raw)
        block_type = str(block.get("type") or "text")
        if block_type == "text":
            blocks.append({"type": "text", "text": str(block.get("text") or "")})
            continue
        detail = str(block.get("detail") or "").strip()
        if block_type == "image_url":
            image_url: Dict[str, Any] = {"url": str(block.get("url") or "")}
            if detail:
                image_url["detail"] = detail
            blocks.append({"type": "image_url", "image_url": image_url})
            continue
        if block_type == "image_base64":
            mime_type = str(block.get("mime_type") or "image/png")
            image_url = {"url": ensure_data_url(str(block.get("data") or ""), mime_type=mime_type)}
            if detail:
                image_url["detail"] = detail
            blocks.append({"type": "image_url", "image_url": image_url})
            continue
        if block_type == "image_file":
            path = str(block.get("path") or "")
            mime_type = str(block.get("mime_type") or "")
            image_url = {
                "url": file_to_data_url(path, mime_type=mime_type or None)
            }
            if detail:
                image_url["detail"] = detail
            blocks.append({"type": "image_url", "image_url": image_url})
            continue
        blocks.append({"type": "text", "text": str(block)})
    return blocks


class OpenAIModel(Model):
    """
    OpenAI model calling implementation

    Environment variable configuration:
    - OPENAI_API_KEY: OpenAI API key
    - OPENAI_BASE_URL: OpenAI API base URL (optional, default https://api.openai.com/v1)

    Output format:
    - If model returns tool_calls: Convert to "Action: tool_name(args)" format
    - If model returns content: Return directly
    - Supports function calling format

    Example:
        llm = OpenAIModel(model="gpt-4")
        result = llm([{"role": "user", "content": "Help me search for Python tutorials"}])
        # Returns: "Action: search(query='Python tutorials')"
    """

    def __init__(
        self,
        model: str = "gpt-4",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 60,
        context_window: Optional[int] = None,
    ):
        """
        Initialize OpenAI model

        Args:
            model: Model name, default gpt-4
            api_key: API key, default read from environment variable
            base_url: API base URL, default read from environment variable
            system_prompt: System prompt
            temperature: Temperature parameter (0.0-1.0)
            max_tokens: Maximum output token count
            timeout: Request timeout (seconds)
            context_window: Total model context window
        """
        super().__init__(
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window=context_window,
        )

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        self.timeout = timeout

        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY not set. Please set environment variable or pass api_key parameter."
            )

    def _call_api(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        """
        Call OpenAI API

        Args:
            messages: OpenAI-style messages list

        Returns:
            Text that can be parsed by parse_tool_calls()
        """
        import openai

        try:
            client = openai.OpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )

            response = self._chat_completion(client, messages, **kwargs)
            return self._parse_response(response)

        except openai.APIError as e:
            return f"API Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _parse_response(self, response) -> str:
        """
        Parse OpenAI response and convert to target format

        Args:
            response: OpenAI API response object

        Returns:
            Text in parse_tool_calls compatible format
        """
        choice = response.choices[0]
        message = choice.message

        # Prioritize processing tool_calls
        if message.tool_calls:
            return self._format_tool_calls(message.tool_calls)

        # Return content
        if message.content:
            return message.content.strip()

        return ""

    def _chat_completion(
        self, client: Any, messages: List[Dict[str, Any]], **kwargs: Any
    ) -> Any:
        response = client.chat.completions.create(
            model=self.model,
            messages=cast(Any, _to_openai_messages(messages)),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        )
        self._set_last_usage(self._usage_from_response(response))
        return response

    def call_raw(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        import openai

        self._last_usage = None
        client = openai.OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )
        return self._chat_completion(client, messages, **kwargs)

    def _usage_from_response(self, response: Any) -> Optional[Dict[str, Any]]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if prompt_tokens is None and isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _format_tool_calls(self, tool_calls) -> str:
        """
        Convert OpenAI tool_calls format to parse_tool_calls compatible format

        Args:
            tool_calls: OpenAI tool_calls list

        Returns:
            Formatted tool call text
        """
        parts = []

        for i, call in enumerate(tool_calls):
            function = call.function
            name = function.name
            args = function.arguments

            try:
                args_dict = json.loads(args) if args else {}
            except json.JSONDecodeError:
                args_dict = {"raw_args": args}

            if len(tool_calls) > 1:
                parts.append(f"Action {i + 1}: {name}")
            else:
                parts.append(f"Action: {name}")

            if args_dict:
                args_str = ", ".join(
                    f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
                    for k, v in args_dict.items()
                )
                parts[-1] += f"({args_str})"

        return "\n".join(parts)

    def supports_tool_schema_delivery(
        self, delivery: str, protocol: Any = None
    ) -> bool:
        _ = protocol
        return str(delivery or "prompt_injection") in {
            "prompt_injection",
            "api_parameter",
            "hybrid",
        }

    def build_tool_schema_request_options(
        self,
        tool_schema_payload: Optional[List[Dict[str, Any]]],
        *,
        protocol: Any = None,
        delivery: str = "prompt_injection",
    ) -> Dict[str, Any]:
        _ = protocol
        if str(delivery or "prompt_injection") not in {"api_parameter", "hybrid"}:
            return {}
        if not tool_schema_payload:
            return {}
        return {"tools": tool_schema_payload}

    def supports_multimodal_input(self) -> bool:
        return True


class OpenAICompatibleModel(Model):
    """
    OpenAI compatible interface model

    Supports any service compatible with OpenAI API format, such as:
    - Azure OpenAI
    - Anthropic (via compatible endpoints)
    - LM Studio
    - LocalAI
    - Tongyi Qianwen
    - Zhipu AI

    Example:
        llm = OpenAICompatibleModel(
            model="qwen-turbo",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    """

    def __init__(
        self,
        model: str = "default",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 60,
        context_window: Optional[int] = None,
    ):
        """
        Initialize compatible model

        Args:
            model: Model name
            api_key: API key
            base_url: API base URL
            system_prompt: System prompt
            temperature: Temperature parameter
            max_tokens: Maximum output token count
            timeout: Request timeout
            context_window: Total model context window
        """
        super().__init__(
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window=context_window,
        )

        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or "dummy-key"
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "")
        self.timeout = timeout

        if not self.base_url:
            raise ValueError(
                "OPENAI_BASE_URL not set. Please set environment variable or pass base_url parameter."
            )

    def _call_api(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        """
        Call OpenAI compatible API

        Args:
            messages: OpenAI-style messages list

        Returns:
            Text that can be parsed by parse_tool_calls()
        """
        import openai

        try:
            client = openai.OpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )

            response = self._chat_completion(client, messages, **kwargs)
            return self._parse_response(response)

        except openai.APIError as e:
            return f"API Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _parse_response(self, response) -> str:
        """
        Parse response
        """
        choice = response.choices[0]
        message = choice.message

        if message.tool_calls:
            return self._format_tool_calls(message.tool_calls)

        if message.content:
            return message.content.strip()

        return ""

    def supports_tool_schema_delivery(
        self, delivery: str, protocol: Any = None
    ) -> bool:
        _ = protocol
        return str(delivery or "prompt_injection") in {
            "prompt_injection",
            "api_parameter",
            "hybrid",
        }

    def build_tool_schema_request_options(
        self,
        tool_schema_payload: Optional[List[Dict[str, Any]]],
        *,
        protocol: Any = None,
        delivery: str = "prompt_injection",
    ) -> Dict[str, Any]:
        _ = protocol
        if str(delivery or "prompt_injection") not in {"api_parameter", "hybrid"}:
            return {}
        if not tool_schema_payload:
            return {}
        return {"tools": tool_schema_payload}

    def supports_multimodal_input(self) -> bool:
        return True

    def _format_tool_calls(self, tool_calls) -> str:
        """
        Format tool calls
        """
        import json

        parts = []

        for i, call in enumerate(tool_calls):
            function = call.function
            name = function.name
            args = function.arguments or "{}"

            try:
                args_dict = json.loads(args)
            except json.JSONDecodeError:
                args_dict = {"raw": args}

            if len(tool_calls) > 1:
                parts.append(f"Action {i + 1}: {name}")
            else:
                parts.append(f"Action: {name}")

            if args_dict:
                args_str = ", ".join(
                    f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
                    for k, v in args_dict.items()
                )
                parts[-1] += f"({args_str})"

        return "\n".join(parts)

    def _chat_completion(
        self, client: Any, messages: List[Dict[str, Any]], **kwargs: Any
    ) -> Any:
        response = client.chat.completions.create(
            model=self.model,
            messages=cast(Any, _to_openai_messages(messages)),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        )
        self._set_last_usage(self._usage_from_response(response))
        return response

    def call_raw(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        import openai

        self._last_usage = None
        client = openai.OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )
        return self._chat_completion(client, messages, **kwargs)

    def _usage_from_response(self, response: Any) -> Optional[Dict[str, Any]]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if prompt_tokens is None and isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }


class AzureOpenAIModel(OpenAICompatibleModel):
    """
    Azure OpenAI model implementation

    Specifically optimized for Azure OpenAI service

    Environment variable configuration:
    - AZURE_OPENAI_API_KEY: Azure API key
    - AZURE_OPENAI_ENDPOINT: Azure endpoint URL
    - AZURE_OPENAI_DEPLOYMENT: Deployment name
    - AZURE_OPENAI_API_VERSION: API version (default 2024-02-15-preview)

    Example:
        llm = AzureOpenAIModel(
            deployment="gpt-4",
            api_version="2024-02-15-preview"
        )
    """

    def __init__(
        self,
        deployment: Optional[str] = None,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        api_version: str = "2024-02-15-preview",
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 60,
        context_window: Optional[int] = None,
    ):
        """
        Initialize Azure OpenAI model

        Args:
            deployment: Deployment name (used as model)
            api_key: API key, default read from environment variable
            endpoint: Endpoint URL, default read from environment variable
            api_version: API version
            system_prompt: System prompt
            temperature: Temperature parameter
            max_tokens: Maximum output token count
            timeout: Request timeout
            context_window: Total model context window
        """
        api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
        endpoint = endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")

        if not endpoint:
            raise ValueError(
                "AZURE_OPENAI_ENDPOINT not set. Please set environment variable or pass endpoint parameter."
            )

        base_url = (
            f"{endpoint.rstrip('/')}/openai/deployments/{deployment or 'default'}"
        )

        super().__init__(
            model=deployment or "azure",
            api_key=api_key,
            base_url=base_url,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            context_window=context_window,
        )

        self.api_version = api_version
        self.deployment = deployment
        self.endpoint = endpoint

    def _call_api(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        """
        Call Azure OpenAI API (adds api_version parameter)
        """
        import openai

        try:
            client = openai.AzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.endpoint,
                api_version=self.api_version,
                timeout=self.timeout,
            )

            response = client.chat.completions.create(
                model=self.deployment or "",
                messages=cast(Any, _to_openai_messages(messages)),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                **kwargs,
            )
            self._set_last_usage(self._usage_from_response(response))

            return self._parse_response(response)

        except openai.APIError as e:
            return f"API Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"


# Register to factory
from .base import ModelFactory

ModelFactory.register("openai")(OpenAIModel)
ModelFactory.register("azure")(AzureOpenAIModel)
ModelFactory.register("openai-compatible")(OpenAICompatibleModel)
