"""
Local Model Implementation

本地模型调用实现，支持 Ollama 和其他本地推理服务。

Ollama 环境变量配置：
- OLLAMA_HOST: Ollama 服务地址 (默认 http://localhost:11434)
- OLLAMA_BASE_URL: Ollama 基础 URL (可选)

其他本地服务支持：
- LM Studio (兼容 OpenAI API)
- LocalAI
- vLLM (兼容 OpenAI API)
"""

import os
import json
from typing import Any, Dict, List, Optional

from .base import Model


class OllamaModel(Model):
    """
    Ollama 本地模型实现

    通过 Ollama REST API 调用本地大模型。

    环境变量配置：
    - OLLAMA_HOST: Ollama 服务地址 (默认 http://localhost:11434)
    - OLLAMA_BASE_URL: Ollama 基础 URL (可选，优先级高于 OLLAMA_HOST)

    输出格式：
    - 支持 Ollama 的 tool calling 格式
    - 自动转换为 parse_tool_calls 可解析的格式

    示例：
        llm = OllamaModel(model="llama3", temperature=0.7)
        result = llm([{"role": "user", "content": "帮我搜索"}])
        # 返回: "Action: search(query='...')"
    """

    def __init__(
        self,
        model: str = "llama3",
        host: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        timeout: int = 120,
        format: str = "json",
        context_window: Optional[int] = None,
    ):
        """
        初始化 Ollama 模型

        Args:
            model: Ollama 模型名称 (默认 llama3)
            host: Ollama 服务地址，默认从环境变量读取
            system_prompt: 系统提示词
            temperature: 温度参数 (0.0-1.0)
            timeout: 请求超时时间（秒）
            format: 响应格式 ("json" 或 "")
            context_window: 模型上下文窗口
        """
        super().__init__(
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=4096,
            context_window=context_window,
        )

        self.host = (
            host
            or os.getenv("OLLAMA_BASE_URL")
            or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        )
        self.timeout = timeout
        self.format = format

        if not self.host:
            raise ValueError("Ollama host 未设置。请设置环境变量或传入 host 参数。")

    def _call_api(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """
        调用 Ollama API

        Args:
            messages: OpenAI 风格的 messages 列表

        Returns:
            可被 parse_tool_calls() 解析的文本
        """
        import urllib.request
        import urllib.error

        _ = kwargs
        url = f"{self.host}/api/chat"

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        if self.format:
            payload["format"] = self.format

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                self._set_last_usage(self._usage_from_response(result))
                return self._parse_response(result)

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            return f"HTTP Error {e.code}: {error_body}"
        except urllib.error.URLError as e:
            return f"Connection Error: {str(e.reason)}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _parse_response(self, response: Dict[str, Any]) -> str:
        """
        解析 Ollama 响应

        Args:
            response: Ollama API 响应

        Returns:
            符合 parse_tool_calls 格式的文本
        """
        message = response.get("message", {})
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            return self._format_tool_calls(tool_calls)
        content = message.get("content", "")

        if content:
            return content.strip()

        return ""

    def _usage_from_response(
        self, response: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        prompt_tokens = response.get("prompt_eval_count")
        completion_tokens = response.get("eval_count")
        if prompt_tokens is None and completion_tokens is None:
            return None
        total_tokens = None
        if isinstance(prompt_tokens, int) or isinstance(completion_tokens, int):
            total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _format_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> str:
        """
        格式化 Ollama tool calls

        Args:
            tool_calls: Ollama tool calls 列表

        Returns:
            格式化的工具调用文本
        """
        parts = []

        for i, call in enumerate(tool_calls):
            tool = call.get("function", {})
            name = tool.get("name", "")
            args = tool.get("arguments", {})

            if len(tool_calls) > 1:
                parts.append(f"Action {i + 1}: {name}")
            else:
                parts.append(f"Action: {name}")

            if args:
                args_str = ", ".join(
                    f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
                    for k, v in args.items()
                )
                parts[-1] += f"({args_str})"

        return "\n".join(parts)


class OllamaGenerateModel(Model):
    """
    Ollama Generate API 模型

    使用 Ollama 的 /api/generate 接口（非 chat 模式）
    适用于不支持 chat 的模型

    示例：
        llm = OllamaGenerateModel(model="llama3-uncensored")
    """

    def __init__(
        self,
        model: str = "llama3",
        host: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        timeout: int = 120,
        context_window: Optional[int] = None,
    ):
        """
        初始化 Ollama Generate 模型

        Args:
            model: Ollama 模型名称
            host: Ollama 服务地址
            system_prompt: 系统提示词
            temperature: 温度参数
            timeout: 超时时间
            context_window: 模型上下文窗口
        """
        super().__init__(
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=4096,
            context_window=context_window,
        )

        self.host = (
            host
            or os.getenv("OLLAMA_BASE_URL")
            or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        )
        self.timeout = timeout

    def _call_api(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """
        调用 Ollama Generate API

        Args:
            messages: OpenAI 风格的 messages 列表

        Returns:
            可被 parse_tool_calls() 解析的文本
        """
        import urllib.request
        import urllib.error

        _ = kwargs
        url = f"{self.host}/api/generate"

        prompt = self._build_prompt(messages)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return self._parse_response(result)

        except Exception as e:
            return f"Error: {str(e)}"

    def _build_prompt(self, messages: List[Dict[str, str]]) -> str:
        """
        构建 Ollama 提示词

        Args:
            messages: OpenAI 风格的 messages 列表

        Returns:
            Ollama 格式的提示词
        """
        parts = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                parts.append(f"System: {content}")
            elif role == "user":
                parts.append(f"User: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")

        return "\n\n".join(parts)

    def _parse_response(self, response: Dict[str, Any]) -> str:
        """
        解析响应
        """
        return response.get("response", "").strip()


class LMStudioModel(Model):
    """
    LM Studio 本地模型实现

    LM Studio 提供 OpenAI 兼容的 API

    环境变量配置：
    - LM_STUDIO_BASE_URL: LM Studio 服务地址 (默认 http://localhost:1234/v1)

    示例：
        llm = LMStudioModel(model="local-model", temperature=0.7)
    """

    def __init__(
        self,
        model: str = "local-model",
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
        context_window: Optional[int] = None,
    ):
        """
        初始化 LM Studio 模型

        Args:
            model: 模型名称
            base_url: 服务地址，默认从环境变量读取
            system_prompt: 系统提示词
            temperature: 温度参数
            max_tokens: 最大输出 token 数
            timeout: 超时时间
        """
        super().__init__(
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window=context_window,
        )

        self.base_url = base_url or os.getenv(
            "LM_STUDIO_BASE_URL", "http://localhost:1234/v1"
        )
        self.timeout = timeout

        if not self.base_url:
            raise ValueError(
                "LM Studio base_url 未设置。请设置环境变量或传入 base_url 参数。"
            )

    def _call_api(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """
        调用 LM Studio API (OpenAI 兼容格式)
        """
        import urllib.request
        import urllib.error

        _ = kwargs
        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                self._set_last_usage(self._usage_from_response(result))
                return self._parse_response(result)

        except urllib.error.HTTPError as e:
            return f"HTTP Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _parse_response(self, response: Dict[str, Any]) -> str:
        """
        解析响应
        """
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                return self._format_tool_calls(tool_calls)
            content = message.get("content", "")

            if content:
                return content.strip()

        return ""

    def _usage_from_response(
        self, response: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None
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

    def _format_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> str:
        parts = []
        for i, call in enumerate(tool_calls):
            function = call.get("function", {})
            name = function.get("name", "")
            raw_args = function.get("arguments", {})
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {"raw_args": raw_args}
            else:
                args = dict(raw_args or {})
            if len(tool_calls) > 1:
                parts.append(f"Action {i + 1}: {name}")
            else:
                parts.append(f"Action: {name}")
            if args:
                args_str = ", ".join(
                    f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
                    for k, v in args.items()
                )
                parts[-1] += f"({args_str})"
        return "\n".join(parts)


class VLLMModel(Model):
    """
    vLLM 本地模型实现

    vLLM 提供 OpenAI 兼容的 API

    环境变量配置：
    - VLLM_BASE_URL: vLLM 服务地址 (默认 http://localhost:8000/v1)

    示例：
        llm = VLLMModel(model="meta-llama/Llama-2-7b-hf")
    """

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
        context_window: Optional[int] = None,
    ):
        """
        初始化 vLLM 模型

        Args:
            model: 模型名称（必填）
            base_url: 服务地址，默认从环境变量读取
            system_prompt: 系统提示词
            temperature: 温度参数
            max_tokens: 最大输出 token 数
            timeout: 超时时间
        """
        super().__init__(
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window=context_window,
        )

        self.base_url = base_url or os.getenv(
            "VLLM_BASE_URL", "http://localhost:8000/v1"
        )
        self.timeout = timeout

    def _call_api(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """
        调用 vLLM API (OpenAI 兼容格式)
        """
        import urllib.request
        import urllib.error

        _ = kwargs
        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return self._parse_response(result)

        except Exception as e:
            return f"Error: {str(e)}"

    def _parse_response(self, response: Dict[str, Any]) -> str:
        """
        解析响应
        """
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")

            if content:
                return content.strip()

        return ""


# 注册到工厂
from .base import ModelFactory

ModelFactory.register("ollama")(OllamaModel)
ModelFactory.register("ollama-generate")(OllamaGenerateModel)
ModelFactory.register("lmstudio")(LMStudioModel)
ModelFactory.register("vllm")(VLLMModel)
ModelFactory.register("local")(LMStudioModel)
