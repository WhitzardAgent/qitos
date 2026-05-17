"""Debug test: run just the Generator in PentAGIFlow context.

This isolates the generator step to debug why reduce() isn't extracting subtasks.
"""

import json
import sys
import time

sys.path.insert(0, "/Users/morinop/qitos")

from qitos.examples.pentagi.config.defaults import PentAGIConfig
from qitos.examples.pentagi.orchestrator.flow import PentAGIFlow

config = PentAGIConfig(
    model_provider="openai-compatible",
    model_name="ds-v4-pro",
    api_key="MajUa5noC1OtfZ3RxznY23AZYWYisTPGc4MKZJyXB9Q=",
    base_url="https://o8kjqm58o8ogcm5ek8aggddkb5ggk8dp.openapi-sj.sii.edu.cn/v1",
    docker_profile="kali",
    authorized_targets=["bbs.kanxue.com"],
    language="zh",
    max_subtasks=5,
    max_steps_per_subtask=8,
    max_total_steps=40,
    ask_user_enabled=False,
    temperature=0.3,
    max_tokens=4096,
)

def main():
    task = "对 bbs.kanxue.com 进行简要的安全信息收集测试，包括：1) HTTP响应头安全分析 2) 基本端口探测 3) Web指纹识别。仅做信息收集，不做任何攻击性测试。"

    print("Building PentAGIFlow system...")
    flow = PentAGIFlow(config)
    flow._build_system()

    generator = flow._agents["generator"]
    print(f"\nGenerator tool_registry: {generator.tool_registry}")
    if generator.tool_registry:
        tools = generator.tool_registry.list_tools()
        print(f"Generator tools: {tools}")
    print(f"Generator llm: {generator.llm}")
    print(f"Generator model_protocol: {getattr(generator, 'model_protocol', None)}")
    print(f"Generator model_parser: {getattr(generator, 'model_parser', None)}")

    print("\nRunning generator only...")

    # Check what system prompt gets built
    gen = flow._agents["generator"]
    test_state = gen.init_state(task)
    prompt_bundle = gen.build_prompt_bundle(test_state)
    sp = prompt_bundle.system_prompt_static or ""
    sp_dynamic = prompt_bundle.system_prompt_dynamic or ""
    full_sp = sp + "\n" + sp_dynamic
    print(f"System prompt length: {len(full_sp)} chars")
    # Check what the prompt builder actually returned
    print(f"system_prompt_static length: {len(sp)}")
    print(f"system_prompt_dynamic length: {len(sp_dynamic)}")
    print(f"tool_schema_payload: {prompt_bundle.tool_schema_payload is not None}")
    if prompt_bundle.tool_schema_payload:
        print(f"tool_schema_payload type: {type(prompt_bundle.tool_schema_payload)}")
        tstr = str(prompt_bundle.tool_schema_payload)
        print(f"tool_schema_payload length: {len(tstr)}")
        print(f"tool_schema_payload preview: {tstr[:300]}")
    print(f"metadata: {prompt_bundle.metadata}")
    # Print the FULL system prompt
    print(f"\n--- FULL System prompt ---")
    print(full_sp)

    start = time.time()

    try:
        subtasks = flow._generate_subtasks(task)
        elapsed = time.time() - start

        print(f"\nGenerator returned in {elapsed:.1f}s")
        print(f"Subtasks: {json.dumps(subtasks, indent=2, ensure_ascii=False)}")
    except Exception as e:
        elapsed = time.time() - start
        print(f"\nError after {elapsed:.1f}s: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
