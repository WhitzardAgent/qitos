"""Debug: test _generate_subtasks with detailed engine output."""

import json
import sys
import time

sys.path.insert(0, "/Users/morinop/qitos")

from qitos.examples.pentagi import PentAGIConfig
from qitos.examples.pentagi.agents.generator import GeneratorAgent
from qitos.examples.pentagi.critic import ReflectorCritic
from qitos.examples.pentagi.orchestrator.flow import PentAGIFlow
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

# Reproduce what _generate_subtasks does
engine = generator.build_engine(
    budget=RuntimeBudget(max_steps=5),
    critics=[ReflectorCritic()],
)

result = engine.run(task)

print(f"Step count: {result.step_count}")
print(f"State generated_subtasks: {result.state.generated_subtasks}")
print(f"State final_result: {result.state.final_result}")
print(f"State stop_reason: {result.state.stop_reason}")

# Print step summaries
for summary in result.step_summaries:
    print(f"\n  Step {summary.step_id}: {summary.tool_name}")
    print(f"    Status: {summary.status}")
    print(f"    Error: {summary.error}")
    print(f"    Result: {summary.result_preview[:300]}")

# Print events
for event in result.events:
    stage = event.payload.get("stage", "") if event.payload else ""
    if stage == "model_output":
        raw = event.payload.get("raw_output", "")
        print(f"\n  Step {event.step_id} raw_output (len={len(raw)}):")
        print(f"    {raw[:500]}")
    elif stage == "parser_result":
        print(f"\n  Step {event.step_id} parser: mode={event.payload.get('parsed_mode')}, severity={event.payload.get('severity')}")
