"""Debug test for generator subtask extraction."""

import json
import sys
import time

sys.path.insert(0, "/Users/morinop/qitos")

from qitos.examples.pentagi import PentAGIConfig
from qitos.examples.pentagi.agents.generator import GeneratorAgent
from qitos.examples.pentagi.tools.barrier import SubtaskListTool
from qitos.core.tool_registry import ToolRegistry
from qitos.engine.states import RuntimeBudget

# Configure with DS-V4-Pro
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

from qitos.models import ModelFactory

llm = ModelFactory.create("openai-compatible",
    model="ds-v4-pro",
    api_key=config.api_key,
    base_url=config.base_url,
    temperature=config.temperature,
    max_tokens=config.max_tokens,
)

# Build generator with proper tools
registry = ToolRegistry()
registry.include_toolset([SubtaskListTool()])

agent = GeneratorAgent(llm=llm, max_subtasks=5, language="zh")
agent.tool_registry = registry

# Run with debug
task = "对 bbs.kanxue.com 进行简要的安全信息收集测试，包括：1) HTTP响应头安全分析 2) 基本端口探测 3) Web指纹识别。仅做信息收集，不做任何攻击性测试。"

print(f"Task: {task}")
print("-" * 60)

# Create engine with verbose tracing
from qitos.engine.engine import Engine

engine = agent.build_engine(
    budget=RuntimeBudget(max_steps=5),
)

start = time.time()
result = engine.run(task)
elapsed = time.time() - start

print(f"\nEngine result:")
print(f"  Step count: {result.step_count}")
print(f"  Runtime: {elapsed:.1f}s")
print(f"  State generated_subtasks: {result.state.generated_subtasks}")
print(f"  State final_result: {result.state.final_result}")
print(f"  State stop_reason: {result.state.stop_reason}")

# Print step summaries
for summary in result.step_summaries:
    print(f"\n  Step {summary.step_id}: {summary.tool_name}")
    print(f"    Status: {summary.status}")
    print(f"    Result: {summary.result_preview[:300]}")

# Print events related to parsing
for event in result.events:
    if event.payload and ("parser" in str(event.payload.get("stage", "")) or "raw_output" in str(event.payload)):
        stage = event.payload.get("stage", "")
        if stage in ("model_output", "parser_result", "parser_diagnostics"):
            print(f"\n  Event step={event.step_id} stage={stage}")
            for key in ("raw_output", "parsed_mode", "parser", "severity", "code"):
                if key in event.payload:
                    val = str(event.payload[key])[:500]
                    print(f"    {key}: {val}")
