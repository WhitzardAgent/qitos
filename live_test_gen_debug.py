"""Debug: check finish_reason and raw output truncation."""

import json
import sys
import time

sys.path.insert(0, "/Users/morinop/qitos")

from qitos.examples.pentagi import PentAGIConfig
from qitos.examples.pentagi.agents.generator import GeneratorAgent
from qitos.examples.pentagi.tools.barrier import SubtaskListTool
from qitos.core.tool_registry import ToolRegistry
from qitos.engine.states import RuntimeBudget
from qitos.models import ModelFactory
from qitos.engine.engine import Engine
from qitos.engine.hooks import EngineHook, HookContext

config = PentAGIConfig(
    model_provider="openai-compatible",
    model_name="ds-v4-pro",
    api_key="MajUa5noC1OtfZ3RxznY23AZYWYisTPGc4MKZJyXB9Q=",
    base_url="https://o8kjqm58o8ogcm5ek8aggddkb5ggk8dp.openapi-sj.sii.edu.cn/v1",
    language="zh",
    max_subtasks=5,
    temperature=0.3,
    max_tokens=4096,  # Increased from 2048
)

llm = ModelFactory.create("openai-compatible",
    model="ds-v4-pro",
    api_key=config.api_key,
    base_url=config.base_url,
    temperature=config.temperature,
    max_tokens=config.max_tokens,
)

registry = ToolRegistry()
registry.include_toolset([SubtaskListTool()])

agent = GeneratorAgent(llm=llm, max_subtasks=5, language="zh")
agent.tool_registry = registry

task = "对 bbs.kanxue.com 进行简要安全信息收集"

engine = agent.build_engine(budget=RuntimeBudget(max_steps=3))

result = engine.run(task)

# Print model response details
for record in result.records:
    if record.model_response:
        mr = record.model_response
        print(f"\nStep {record.step_id} model_response:")
        print(f"  finish_reason: {mr.get('finish_reason', 'N/A')}")
        text = mr.get('text', '')
        print(f"  text length: {len(text)}")
        # Try to parse the text
        try:
            parsed = json.loads(text)
            print(f"  JSON parse: OK")
            print(f"  action.name: {parsed.get('action', {}).get('name', 'N/A')}")
        except json.JSONDecodeError as e:
            print(f"  JSON parse FAILED: {e}")
            print(f"  Last 100 chars: ...{text[-100:]}")

print(f"\nGenerated subtasks: {result.state.generated_subtasks}")
