"""Debug: test _generate_subtasks directly."""

import json
import sys
import time

sys.path.insert(0, "/Users/morinop/qitos")

from qitos.examples.pentagi import PentAGIConfig, PentAGIFlow

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

# Check generator has tool_registry
gen = flow._agents["generator"]
print(f"Generator tool_registry: {gen.tool_registry}")
if gen.tool_registry:
    print(f"  Tools: {gen.tool_registry.list_tools()}")

# Check LLM
llm = gen.llm
print(f"\nLLM model: {getattr(llm, 'model', None)}")
print(f"LLM max_tokens: {getattr(llm, 'max_tokens', None)}")

# Now run _generate_subtasks
print("\n--- Running _generate_subtasks ---")
start = time.time()
subtasks = flow._generate_subtasks(task)
elapsed = time.time() - start

print(f"Time: {elapsed:.1f}s")
print(f"Subtasks: {json.dumps(subtasks, ensure_ascii=False, indent=2)[:2000]}")
