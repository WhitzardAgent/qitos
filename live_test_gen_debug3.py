"""Debug: run generator with exact flow.py config and print exceptions."""

import json
import sys
import time
import traceback

sys.path.insert(0, "/Users/morinop/qitos")

from qitos.examples.pentagi import PentAGIConfig
from qitos.examples.pentagi.agents.generator import GeneratorAgent
from qitos.examples.pentagi.tools.barrier import SubtaskListTool
from qitos.examples.pentagi.tools.generate_subtasks import GenerateSubtasksTool
from qitos.examples.pentagi.critic import ReflectorCritic
from qitos.examples.pentagi.orchestrator.flow import PentAGIFlow
from qitos.core.tool_registry import ToolRegistry
from qitos.engine.states import RuntimeBudget

config = PentAGIConfig(
    model_provider="openai-compatible",
    model_name="ds-v4-pro",
    api_key="MajUa5noC1OtfZ3RxznY23AZYWYisTPGc4MKZJyXB9Q=",
    base_url="https://o8kjqm58o8ogcm5ek8aggddkb5ggk8dp.openapi-sj.sii.edu.cn/v1",
    language="zh",
    max_subtasks=5,
    temperature=0.3,
    max_tokens=4096,
)

flow = PentAGIFlow(config)
flow._build_system()

task = "对 bbs.kanxue.com 进行简要安全信息收集"
generator = flow._agents["generator"]

print(f"Generator tools: {generator.tool_registry.list_tools()}")

# Build engine exactly like flow.py does
engine = generator.build_engine(
    budget=RuntimeBudget(max_steps=5),
    critics=[ReflectorCritic()],
)

try:
    result = engine.run(task)
    print(f"\nStep count: {result.step_count}")
    print(f"State generated_subtasks: {len(result.state.generated_subtasks)} items")
    if result.state.generated_subtasks:
        for st in result.state.generated_subtasks:
            print(f"  - {st.get('title', '?')}")
    print(f"State stop_reason: {result.state.stop_reason}")

    # Check step 0 tool call
    for summary in result.step_summaries:
        print(f"\n  Step {summary.step_id}: {summary.tool_name} -> {summary.status}")
        if summary.error:
            print(f"    Error: {summary.error}")
except Exception as e:
    print(f"EXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()
