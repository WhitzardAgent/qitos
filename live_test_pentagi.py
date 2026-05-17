"""Live test for PentAGI using Deepseek-V4-Pro endpoint.

This script tests the PentAGI system against a real target (bbs.kanxue.com)
using a production LLM endpoint. It exercises the full pipeline:
generation → execution → reporting.
"""

import json
import sys
import time

# Add project root to path
sys.path.insert(0, "/Users/morinop/qitos")

from qitos.examples.pentagi import (
    PentAGIRunner,
    PentAGIConfig,
)

# Configure PentAGI with Deepseek-V4-Pro endpoint
config = PentAGIConfig(
    model_provider="openai-compatible",
    model_name="ds-v4-pro",
    api_key="MajUa5noC1OtfZ3RxznY23AZYWYisTPGc4MKZJyXB9Q=",
    base_url="https://o8kjqm58o8ogcm5ek8aggddkb5ggk8dp.openapi-sj.sii.edu.cn/v1",
    docker_profile="kali",
    authorized_targets=["www.sii.edu.cn"],
    language="zh",
    max_subtasks=5,          # Keep small for test
    max_steps_per_subtask=8, # Keep small for test
    max_total_steps=40,
    ask_user_enabled=False,
    temperature=0.3,
    max_tokens=16384,
    context_window=65536,
)


def main():
    print("=" * 60)
    print("PentAGI Live Test — www.sii.edu.cn")
    print("=" * 60)
    print(f"\nModel: {config.model_name}")
    print(f"Target: www.sii.edu.cn")
    print(f"Max subtasks: {config.max_subtasks}")
    print(f"Max steps/subtask: {config.max_steps_per_subtask}")
    print()

    task = "对 www.sii.edu.cn 进行简要的安全信息收集测试，包括：1) HTTP响应头安全分析 2) 基本端口探测 3) Web指纹识别。仅做信息收集，不做任何攻击性测试。"

    print(f"Task: {task}")
    print("-" * 60)

    runner = PentAGIRunner(config)
    start_time = time.time()

    try:
        result = runner.run(task)
        elapsed = time.time() - start_time

        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        print(f"\nStatus: {result.status}")
        print(f"Total steps: {result.total_steps}")
        print(f"Time: {elapsed:.1f}s")
        print(f"\nSubtasks generated: {len(result.subtasks)}")
        print(f"Subtasks completed: {len(result.completed_subtasks)}")

        if result.subtasks:
            print("\n--- Subtask Plan ---")
            for i, st in enumerate(result.subtasks):
                title = st.get("title", "Untitled")
                status = st.get("status", "unknown")
                print(f"  {i+1}. [{status}] {title}")

        if result.completed_subtasks:
            print("\n--- Completed Subtasks ---")
            for i, st in enumerate(result.completed_subtasks):
                title = st.get("title", "Untitled")
                result_text = str(st.get("result", ""))[:200]
                print(f"  {i+1}. {title}")
                print(f"     Result: {result_text}...")

        if result.findings:
            print(f"\n--- Findings ({len(result.findings)}) ---")
            for f in result.findings:
                print(f"  - {f.get('title', '?')}: {f.get('description', '')[:100]}")

        print("\n--- Report ---")
        print(result.report[:3000] if result.report else "No report generated")

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\nError after {elapsed:.1f}s: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
