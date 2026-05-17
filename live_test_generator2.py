"""Debug test — capture full parser diagnostics."""

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

config = PentAGIConfig(
    model_provider="openai-compatible",
    model_name="ds-v4-pro",
    api_key="MajUa5noC1OtfZ3RxznY23AZYWYisTPGc4MKZJyXB9Q=",
    base_url="https://o8kjqm58o8ogcm5ek8aggddkb5ggk8dp.openapi-sj.sii.edu.cn/v1",
    language="zh",
    max_subtasks=5,
    temperature=0.3,
    max_tokens=2048,
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

task = "对 bbs.kanxue.com 进行简要的安全信息收集测试"

engine = agent.build_engine(budget=RuntimeBudget(max_steps=3))

# Add a hook to capture the raw LLM output
class DebugHook:
    def on_after_step(self, ctx):
        record = ctx.record
        if hasattr(record, 'parser_diagnostics') and record.parser_diagnostics:
            d = record.parser_diagnostics
            print(f"\n=== Parser Diagnostics Step {record.step_id} ===")
            for key in ('code', 'summary', 'details', 'severity', 'repair_instruction', 'expected_shape'):
                if key in d:
                    print(f"  {key}: {str(d[key])[:500]}")

engine.hooks.append(DebugHook())

result = engine.run(task)

print(f"\nState generated_subtasks: {result.state.generated_subtasks}")
print(f"Step count: {result.step_count}")

# Print the raw output from first step
for event in result.events:
    if event.payload and event.payload.get("stage") == "model_output":
        raw = event.payload.get("raw_output", "")
        print(f"\nFull raw output (len={len(raw)}):")
        print(raw[:3000])
        break
