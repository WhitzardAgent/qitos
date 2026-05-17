"""Quick test: manually parse the known good output."""

import json
import sys
sys.path.insert(0, "/Users/morinop/qitos")

from qitos.kit.parser.json_parser import JsonDecisionParser

# The actual output from the model (copied from the debug output)
raw = '{"thought": "test","action":{"name":"subtask_list","args":{"subtasks":"[{\\"id\\":\\"1\\",\\"title\\":\\"DNS\\",\\"description\\":\\"DNS info\\"}]","message":"test"}}}'

parser = JsonDecisionParser()
result = parser.parse(raw, context={"step": 0})

print(f"Mode: {result.mode}")
print(f"Actions: {result.actions}")
print(f"Meta: {result.meta}")
print(f"Rationale: {result.rationale}")

# Now test with the actual full output
actual_output = """{"thought": "用户要求对 bbs.kanxue.com 进行简要的安全信息收集测试。我需要生成最多5个子任务来完成这个目标。","action":{"name":"subtask_list","args":{"subtasks":"[{\\"id\\":\\"1\\",\\"title\\":\\"DNS\\",\\"description\\":\\"DNS info\\"}]","message":"已生成5个安全信息收集子任务"}}}"""

result2 = parser.parse(actual_output, context={"step": 0})
print(f"\nMode2: {result2.mode}")
print(f"Actions2: {result2.actions}")
print(f"Meta2: {result2.meta}")

# Test the real raw output that was captured (the full one)
# Let me try parsing it directly
try:
    parsed = json.loads(actual_output)
    print(f"\nDirect JSON parse: OK")
    print(f"  thought: {parsed.get('thought', '')[:50]}")
    print(f"  action: {parsed.get('action')}")
except json.JSONDecodeError as e:
    print(f"\nDirect JSON parse FAILED: {e}")
