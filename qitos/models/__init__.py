"""
QitOS Models Module

统一的大模型调用接口。

提供多种模型支持：
- OpenAI (GPT-4, GPT-3.5)
- Anthropic (Claude native Messages API)
- Google Gemini (native generateContent API)
- LiteLLM
- OpenAI 兼容 (Azure, 通义千问, 智谱 AI 等)
- 本地模型 (Ollama, LM Studio, vLLM)

Usage:
    # OpenAI
    from qitos.models import OpenAIModel
    llm = OpenAIModel(model="gpt-4")

    # Ollama
    from qitos.models import OllamaModel
    llm = OllamaModel(model="llama3")

    # 从环境变量自动选择
    from qitos.models import ModelFactory
    llm = ModelFactory.from_env()
"""

from .base import Model, AsyncModel, ModelFactory
from .context_registry import infer_context_window
from .profile_registry import (
    ModelProfile,
    infer_default_protocol,
    infer_model_profile,
    known_model_profiles,
)
from .anthropic import AnthropicModel
from .gemini import GeminiModel
from .litellm import LiteLLMModel
from .openai import (
    OpenAIModel,
    OpenAICompatibleModel,
    AzureOpenAIModel,
    AsyncOpenAIModel,
    AsyncOpenAICompatibleModel,
)
from .local import OllamaModel, OllamaGenerateModel, LMStudioModel, VLLMModel

__all__ = [
    # 基类
    "Model",
    "AsyncModel",
    "ModelFactory",
    "infer_context_window",
    "ModelProfile",
    "infer_model_profile",
    "infer_default_protocol",
    "known_model_profiles",
    # OpenAI
    "OpenAIModel",
    "OpenAICompatibleModel",
    "AzureOpenAIModel",
    "AsyncOpenAIModel",
    "AsyncOpenAICompatibleModel",
    "AnthropicModel",
    "GeminiModel",
    "LiteLLMModel",
    # 本地模型
    "OllamaModel",
    "OllamaGenerateModel",
    "LMStudioModel",
    "VLLMModel",
]
