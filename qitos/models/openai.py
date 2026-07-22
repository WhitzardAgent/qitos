"""
OpenAI Model Implementation

OpenAI API-based model calling implementation.
Supports environment variable configuration: OPENAI_API_KEY, OPENAI_BASE_URL
"""

import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, cast

from ..core.multimodal import (
    content_to_text,
    ensure_data_url,
    file_to_data_url,
    has_nontext_content,
    normalize_content_block,
    normalize_messages,
)
from .base import Model, ModelStreamChunk


GLM_TOKENIZER_ENV_VARS = ("QITOS_GLM_TOKENIZER_PATH", "GLM_TOKENIZER_PATH")


def _usage_payload(usage: Any) -> Optional[Dict[str, Any]]:
    """Normalize OpenAI/SGLang usage, including optional cache reporting."""
    if usage is None:
        return None
    def field(name: str) -> Any:
        return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    prompt_tokens = field("prompt_tokens")
    completion_tokens = field("completion_tokens")
    total_tokens = field("total_tokens")
    details = field("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else getattr(details, "cached_tokens", None)
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached,
    }


def _wire_tool_schema(
    tool_schema_payload: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Return provider-valid function tools from richer registry metadata."""
    wire: List[Dict[str, Any]] = []
    for item in list(tool_schema_payload or []):
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict) or not function.get("name"):
            continue
        clean_function = {
            key: function[key]
            for key in ("name", "description", "parameters", "strict")
            if key in function and function[key] is not None
        }
        wire.append({"type": "function", "function": clean_function})
    return wire


def _relocate_chat_template_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Move provider-specific generation kwargs into ``extra_body``.

    The OpenAI Python SDK does not accept ``chat_template_kwargs`` as a
    top-level parameter.  vLLM-compatible serving endpoints expect it inside
    ``extra_body`` instead.  Calling code that merges ``default_request_kwargs``
    often places it at the top level, so we relocate it here.
    """
    result = dict(kwargs)
    extra_body = dict(result.pop("extra_body", None) or {})
    ctk = result.pop("chat_template_kwargs", None)
    if isinstance(ctk, dict) and ctk:
        extra_body["chat_template_kwargs"] = ctk
    # ``do_sample`` is accepted by vLLM/SGLang-style endpoints but not by the
    # OpenAI Python SDK's typed method signature. Passing it at top level made
    # the whole native-tool request fail with TypeError and silently fall back
    # to a schema-less text call. Preserve provider semantics through the SDK's
    # supported extra_body escape hatch.
    do_sample = result.pop("do_sample", None)
    if do_sample is not None:
        extra_body["do_sample"] = do_sample
    if extra_body:
        result["extra_body"] = extra_body
    return result


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
        elif content is None and role == "assistant" and payload.get("tool_calls"):
            payload["content"] = None
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


def _is_glm_model_name(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return normalized.startswith("glm-") or normalized.startswith("zai-org/glm-")


def _glm_tokenizer_path() -> Optional[str]:
    for name in GLM_TOKENIZER_ENV_VARS:
        value = os.getenv(name, "").strip()
        if value and Path(value).exists():
            return value
    return None


@lru_cache(maxsize=4)
def _load_glm_tokenizer(path: str) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        path,
        trust_remote_code=True,
        local_files_only=True,
    )


def _tokenizer_count_result(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return int(value)
    if isinstance(value, list):
        return len(value)
    getter = getattr(value, "get", None)
    if callable(getter):
        ids = getter("input_ids")
        if isinstance(ids, list):
            return len(ids)
    return None


def _normalize_messages_for_tokenizer(payload: List[Any]) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": str(item)})
            continue
        role = str(item.get("role") or "user").strip() or "user"
        content = content_to_text(item.get("content"))
        extras: Dict[str, Any] = {}
        for key in ("tool_calls", "tool_call_id", "name"):
            if key in item and item.get(key) not in (None, "", []):
                extras[key] = item.get(key)
        if extras:
            content = (
                content
                + "\n"
                + json.dumps(extras, ensure_ascii=False, sort_keys=True)
            ).strip()
        messages.append({"role": role, "content": content})
    return messages


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
        timeout: int = 120,
        context_window: Optional[int] = None,
        default_request_kwargs: Optional[Dict[str, Any]] = None,
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
            default_request_kwargs: Extra kwargs merged into every API call
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
        self.default_request_kwargs = default_request_kwargs or {}

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
        safe_kwargs = _relocate_chat_template_kwargs(kwargs)
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=cast(Any, _to_openai_messages(messages)),
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    **safe_kwargs,
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                time.sleep(2 ** attempt)
        if response is None:
            assert last_error is not None
            raise last_error
        self._set_last_usage(self._usage_from_response(response))
        return response

    def call_raw(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        import openai

        self._last_usage = None
        client = openai.OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )
        return self._chat_completion(client, messages, **kwargs)

    def stream(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Iterator[ModelStreamChunk]:
        """Stream OpenAI response as chunks, yielding token-level text."""
        import openai

        self._last_usage = None
        try:
            client = openai.OpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
            response = client.chat.completions.create(
                model=self.model,
                messages=cast(Any, _to_openai_messages(messages)),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
                **kwargs,
            )
            accumulated_tool_calls: List[Dict[str, Any]] = []
            for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                text = delta.content or ""
                if text:
                    yield ModelStreamChunk(text=text, done=False)
                # Accumulate streaming tool call deltas
                delta_tool_calls = getattr(delta, "tool_calls", None)
                if delta_tool_calls:
                    for dtc in delta_tool_calls:
                        idx = getattr(dtc, "index", len(accumulated_tool_calls))
                        while len(accumulated_tool_calls) <= idx:
                            accumulated_tool_calls.append(
                                {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
                            )
                        tc = accumulated_tool_calls[idx]
                        tc_id = getattr(dtc, "id", None)
                        if tc_id:
                            tc["id"] = tc_id
                        tc_type = getattr(dtc, "type", None)
                        if tc_type:
                            tc["type"] = tc_type
                        fn = getattr(dtc, "function", None)
                        if fn:
                            fn_name = getattr(fn, "name", None)
                            if fn_name:
                                tc["function"]["name"] = fn_name
                            fn_args = getattr(fn, "arguments", None)
                            if fn_args:
                                tc["function"]["arguments"] = tc["function"].get("arguments", "") + fn_args
                if chunk.choices[0].finish_reason is not None:
                    usage_data = None
                    if hasattr(chunk, "usage") and chunk.usage:
                        usage_data = {
                            "prompt_tokens": getattr(chunk.usage, "prompt_tokens", None),
                            "completion_tokens": getattr(chunk.usage, "completion_tokens", None),
                            "total_tokens": getattr(chunk.usage, "total_tokens", None),
                            "cached_tokens": getattr(getattr(chunk.usage, "prompt_tokens_details", None), "cached_tokens", None),
                        }
                        self._set_last_usage(usage_data)
                    yield ModelStreamChunk(
                        text="", done=True, usage=usage_data,
                        tool_calls=accumulated_tool_calls if accumulated_tool_calls else None,
                    )
        except openai.APIError as e:
            yield ModelStreamChunk(text=f"API Error: {str(e)}", done=True)
        except Exception as e:
            yield ModelStreamChunk(text=f"Error: {str(e)}", done=True)

    def _usage_from_response(self, response: Any) -> Optional[Dict[str, Any]]:
        return _usage_payload(getattr(response, "usage", None))

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
        wire = _wire_tool_schema(tool_schema_payload)
        if not wire:
            return {}
        return {"tools": wire}

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
        temperature: float | None = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
        context_window: Optional[int] = None,
        default_request_kwargs: Optional[Dict[str, Any]] = None,
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
            default_request_kwargs: Extra kwargs merged into every API call
                (e.g. {"chat_template_kwargs": {"thinking": True}})
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
        self.default_request_kwargs = default_request_kwargs or {}

        if not self.base_url:
            raise ValueError(
                "OPENAI_BASE_URL not set. Please set environment variable or pass base_url parameter."
            )

    def count_tokens(self, messages_or_text: Any) -> Optional[int]:
        if self._should_use_glm_tokenizer():
            value = self._count_tokens_with_glm_tokenizer(messages_or_text)
            if isinstance(value, int) and value >= 0:
                return value
        return super().count_tokens(messages_or_text)

    def _should_use_glm_tokenizer(self) -> bool:
        metadata = dict(getattr(self, "qitos_harness_metadata", {}) or {})
        if str(metadata.get("family_preset") or "").strip().lower() == "glm":
            return True
        return _is_glm_model_name(self.model)

    def _count_tokens_with_glm_tokenizer(self, payload: Any) -> Optional[int]:
        path = _glm_tokenizer_path()
        if not path:
            return None
        try:
            tokenizer = _load_glm_tokenizer(path)
        except Exception:
            return None

        try:
            if isinstance(payload, list):
                messages = _normalize_messages_for_tokenizer(payload)
                encoded = tokenizer.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=False,
                )
                return _tokenizer_count_result(encoded)
            text = self._stringify_token_payload(payload)
            encoded = tokenizer.encode(text, add_special_tokens=False)
            return _tokenizer_count_result(encoded)
        except Exception:
            return None

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
        wire = _wire_tool_schema(tool_schema_payload)
        if not wire:
            return {}
        return {"tools": wire}

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
        safe_kwargs = _relocate_chat_template_kwargs(kwargs)
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request_kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": cast(Any, _to_openai_messages(messages)),
                    "max_tokens": self.max_tokens,
                    **safe_kwargs,
                }
                if self.temperature is not None:
                    request_kwargs["temperature"] = self.temperature
                response = client.chat.completions.create(
                    **request_kwargs,
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                time.sleep(2 ** attempt)
        if response is None:
            assert last_error is not None
            raise last_error
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
        return _usage_payload(getattr(response, "usage", None))

    def stream(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Iterator[ModelStreamChunk]:
        """Stream OpenAI-compatible response as chunks, yielding token-level text."""
        import openai

        self._last_usage = None
        try:
            client = openai.OpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
            # Build stream options — request usage in final chunk
            # Not all OpenAI-compatible APIs support this, so we wrap it
            create_kwargs: Dict[str, Any] = dict(kwargs)
            if "stream_options" not in create_kwargs:
                create_kwargs["stream_options"] = {"include_usage": True}
            try:
                request_kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": cast(Any, _to_openai_messages(messages)),
                    "max_tokens": self.max_tokens,
                    "stream": True,
                    **create_kwargs,
                }
                if self.temperature is not None:
                    request_kwargs["temperature"] = self.temperature
                response = client.chat.completions.create(**request_kwargs)
            except (openai.BadRequestError, openai.APIError):
                # Retry without stream_options if the API doesn't support it
                create_kwargs.pop("stream_options", None)
                request_kwargs = {
                    "model": self.model,
                    "messages": cast(Any, _to_openai_messages(messages)),
                    "max_tokens": self.max_tokens,
                    "stream": True,
                    **create_kwargs,
                }
                if self.temperature is not None:
                    request_kwargs["temperature"] = self.temperature
                response = client.chat.completions.create(**request_kwargs)
            accumulated_tool_calls: List[Dict[str, Any]] = []
            for chunk in response:
                if not chunk.choices:
                    # Empty choices chunk may carry usage data (OpenAI sends it last)
                    if hasattr(chunk, "usage") and chunk.usage:
                        usage_data = {
                            "prompt_tokens": getattr(chunk.usage, "prompt_tokens", None),
                            "completion_tokens": getattr(chunk.usage, "completion_tokens", None),
                            "total_tokens": getattr(chunk.usage, "total_tokens", None),
                            "cached_tokens": getattr(getattr(chunk.usage, "prompt_tokens_details", None), "cached_tokens", None),
                        }
                        self._set_last_usage(usage_data)
                    continue
                delta = chunk.choices[0].delta
                text = delta.content or ""
                if text:
                    yield ModelStreamChunk(text=text, done=False)
                # Accumulate streaming tool call deltas
                delta_tool_calls = getattr(delta, "tool_calls", None)
                if delta_tool_calls:
                    for dtc in delta_tool_calls:
                        idx = getattr(dtc, "index", len(accumulated_tool_calls))
                        # Extend list if needed
                        while len(accumulated_tool_calls) <= idx:
                            accumulated_tool_calls.append(
                                {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
                            )
                        tc = accumulated_tool_calls[idx]
                        tc_id = getattr(dtc, "id", None)
                        if tc_id:
                            tc["id"] = tc_id
                        tc_type = getattr(dtc, "type", None)
                        if tc_type:
                            tc["type"] = tc_type
                        fn = getattr(dtc, "function", None)
                        if fn:
                            fn_name = getattr(fn, "name", None)
                            if fn_name:
                                tc["function"]["name"] = fn_name
                            fn_args = getattr(fn, "arguments", None)
                            if fn_args:
                                tc["function"]["arguments"] = tc["function"].get("arguments", "") + fn_args
                if chunk.choices[0].finish_reason is not None:
                    usage_data = None
                    if hasattr(chunk, "usage") and chunk.usage:
                        usage_data = {
                            "prompt_tokens": getattr(chunk.usage, "prompt_tokens", None),
                            "completion_tokens": getattr(chunk.usage, "completion_tokens", None),
                            "total_tokens": getattr(chunk.usage, "total_tokens", None),
                            "cached_tokens": getattr(getattr(chunk.usage, "prompt_tokens_details", None), "cached_tokens", None),
                        }
                        self._set_last_usage(usage_data)
                    yield ModelStreamChunk(
                        text="", done=True, usage=usage_data,
                        tool_calls=accumulated_tool_calls if accumulated_tool_calls else None,
                    )
        except openai.APIError as e:
            yield ModelStreamChunk(text=f"API Error: {str(e)}", done=True)
        except Exception as e:
            yield ModelStreamChunk(text=f"Error: {str(e)}", done=True)


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


class AsyncOpenAICompatibleModel(OpenAICompatibleModel):
    """
    Async version of OpenAICompatibleModel using openai.AsyncOpenAI.

    Supports any service compatible with the OpenAI API format.
    Use ``await model.acall(messages)`` for non-blocking calls.

    Example::

        llm = AsyncOpenAICompatibleModel(
            model="qwen-turbo",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        result = await llm.acall([{"role": "user", "content": "Hello"}])
    """

    async def _acall_api(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        """Async call to OpenAI-compatible API."""
        import openai

        try:
            client = openai.AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
            response = await self._achat_completion(client, messages, **kwargs)
            return self._parse_response(response)
        except openai.APIError as e:
            return f"API Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    async def _achat_completion(
        self, client: Any, messages: List[Dict[str, Any]], **kwargs: Any
    ) -> Any:
        response = await client.chat.completions.create(
            model=self.model,
            messages=cast(Any, _to_openai_messages(messages)),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        )
        self._set_last_usage(self._usage_from_response(response))
        return response

    async def acall_raw(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        """Async version of call_raw returning provider-native response."""
        import openai

        self._last_usage = None
        client = openai.AsyncOpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )
        return await self._achat_completion(client, messages, **kwargs)

    async def astream(self, messages: List[Dict[str, Any]], **kwargs: Any) -> AsyncIterator[ModelStreamChunk]:
        """Async stream OpenAI-compatible response as chunks."""
        import openai

        self._last_usage = None
        try:
            client = openai.AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
            response = await client.chat.completions.create(
                model=self.model,
                messages=cast(Any, _to_openai_messages(messages)),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
                **kwargs,
            )
            async for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                text = delta.content or ""
                if text:
                    yield ModelStreamChunk(text=text, done=False)
                if chunk.choices[0].finish_reason is not None:
                    usage_data = None
                    if hasattr(chunk, "usage") and chunk.usage:
                        usage_data = {
                            "prompt_tokens": getattr(chunk.usage, "prompt_tokens", None),
                            "completion_tokens": getattr(chunk.usage, "completion_tokens", None),
                            "total_tokens": getattr(chunk.usage, "total_tokens", None),
                        }
                        self._set_last_usage(usage_data)
                    yield ModelStreamChunk(text="", done=True, usage=usage_data)
        except openai.APIError as e:
            yield ModelStreamChunk(text=f"API Error: {str(e)}", done=True)
        except Exception as e:
            yield ModelStreamChunk(text=f"Error: {str(e)}", done=True)


class AsyncOpenAIModel(OpenAIModel):
    """
    Async version of OpenAIModel using openai.AsyncOpenAI.

    Example::

        llm = AsyncOpenAIModel(model="gpt-4")
        result = await llm.acall([{"role": "user", "content": "Hello"}])
    """

    async def _acall_api(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        """Async call to OpenAI API."""
        import openai

        try:
            client = openai.AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
            response = await self._achat_completion(client, messages, **kwargs)
            return self._parse_response(response)
        except openai.APIError as e:
            return f"API Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    async def _achat_completion(
        self, client: Any, messages: List[Dict[str, Any]], **kwargs: Any
    ) -> Any:
        response = await client.chat.completions.create(
            model=self.model,
            messages=cast(Any, _to_openai_messages(messages)),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        )
        self._set_last_usage(self._usage_from_response(response))
        return response

    async def acall_raw(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        """Async version of call_raw returning provider-native response."""
        import openai

        self._last_usage = None
        client = openai.AsyncOpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )
        return await self._achat_completion(client, messages, **kwargs)

    async def astream(self, messages: List[Dict[str, Any]], **kwargs: Any) -> AsyncIterator[ModelStreamChunk]:
        """Async stream OpenAI response as chunks."""
        import openai

        self._last_usage = None
        try:
            client = openai.AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
            response = await client.chat.completions.create(
                model=self.model,
                messages=cast(Any, _to_openai_messages(messages)),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
                **kwargs,
            )
            async for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                text = delta.content or ""
                if text:
                    yield ModelStreamChunk(text=text, done=False)
                if chunk.choices[0].finish_reason is not None:
                    usage_data = None
                    if hasattr(chunk, "usage") and chunk.usage:
                        usage_data = {
                            "prompt_tokens": getattr(chunk.usage, "prompt_tokens", None),
                            "completion_tokens": getattr(chunk.usage, "completion_tokens", None),
                            "total_tokens": getattr(chunk.usage, "total_tokens", None),
                        }
                        self._set_last_usage(usage_data)
                    yield ModelStreamChunk(text="", done=True, usage=usage_data)
        except openai.APIError as e:
            yield ModelStreamChunk(text=f"API Error: {str(e)}", done=True)
        except Exception as e:
            yield ModelStreamChunk(text=f"Error: {str(e)}", done=True)


# Register to factory
from .base import ModelFactory

ModelFactory.register("openai")(OpenAIModel)
ModelFactory.register("azure")(AzureOpenAIModel)
ModelFactory.register("openai-compatible")(OpenAICompatibleModel)
ModelFactory.register("async-openai")(AsyncOpenAIModel)
ModelFactory.register("async-openai-compatible")(AsyncOpenAICompatibleModel)
