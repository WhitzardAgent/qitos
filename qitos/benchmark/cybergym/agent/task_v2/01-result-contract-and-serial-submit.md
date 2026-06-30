# 01 — 修复 Action/Tool Result 契约并强制串行提交

优先级：P0，所有 feedback/candidate 优化的前置条件  
依赖：[00](00-boundaries-and-baseline.md)  
涉及仓库：当前 agent + `../qitos`  
禁止：借机重写 QitOS executor；不得新增本地执行器

## 最新再审计状态

状态：**未开始，仍为最先实施的 P0**。

已复核当前代码：

- `ActionExecutor._build_runtime_context()` 仍无 `action_id`；
- `_ActionRuntime` 的 `ToolResult.metadata` 仍未复制 `ActionResult.action_id`；
- `_last_structured_output` 和 `_last_submit_structured_output` 仍存在；
- `submit_poc` 仍在 QitOS `_CONCURRENCY_SAFE_TOOLS`；
- 最新两模式改动没有触及这些协议。

QitOS 当前 `parser.py` 和 renderer 文件另有用户改动。本任务只修改列出的 executor/runtime 文件；不得格式化或恢复其它 QitOS diff。

## 问题

当前 `Action`/`ActionResult` 已有 `action_id`，但：

- `ActionExecutor._build_runtime_context()` 没把 `action_id` 传给 tool；
- `_ActionRuntime` 把 `ActionResult` 转成 `ToolResult` 时没有保留 `action_id`；
- agent 的 `_render_output()` 在没有 id 时写入 `_last_structured_output` 单槽；
- `submit_tool.py` 使用模块级 `_last_submit_structured_output`；
- QitOS 把 `submit_poc` 标为 concurrency-safe。

因此并行 READ 或 submit 可能把 structured payload 归给错误 action/candidate。

## 必须复用

- `qitos/core/action.py::Action.action_id`
- `qitos/core/action.py::ActionResult.action_id`
- `qitos/core/tool_result.py::ToolResult.metadata`
- `qitos/engine/action_executor.py::_execute_concurrent()` 的原序恢复
- `agent_impl/tools.py::_render_output()`
- `agent.py::_process_action_result()`

不要创建第二套 envelope/dataclass；`action_id` 直接贯穿现有对象。

## 文件

QitOS：

- 修改 `../qitos/qitos/engine/action_executor.py`
- 修改 `../qitos/qitos/engine/_action_runtime.py`
- 测试 `../qitos/tests/test_advanced_tools_and_executor.py`
- 测试 `../qitos/tests/test_native_tool_calling_runtime.py`

Agent：

- 修改 `agent_impl/tools.py`
- 修改 `agent.py`
- 修改 `submit_tool.py`
- 修改 `tests/test_agent.py` 或新增 `tests/test_result_contract.py`

## 实现步骤

### 1. action id 进入 runtime context

把 executor 内部调用从只传 tool name 改为传 `Action` 或显式 `action_id`：

```python
runtime_context = self._build_runtime_context(
    action.name,
    env=env,
    state=state,
    action_id=action.action_id,
)
```

context 至少包含：

```python
{
    "action_id": action.action_id,
    "tool_name": action.name,
    # 保留原有 env/state/ops/... 全部字段
}
```

不要删除或改名现有 context key。

### 2. ActionResult 转 ToolResult 时保留 id

在 `_action_runtime.py` 所有 success/error/blocked 分支生成 `ToolResult` 时写入：

```python
metadata={
    ...,
    "action_id": item.action_id,
    "tool_name": item.name,
}
```

blocked action 使用 `normalized_action.action_id`。`record.tool_invocations` 同样增加 `action_id`，方便 trace replay。

### 3. 移除 agent 的单槽 fallback

调整 `_render_output()`：

- 有 `runtime_context.action_id`：把原始 dict 放到 `_structured_output_buffer[action_id]`，返回现有 rendered text；
- 没有 action id（直接单元调用）：直接返回原始 dict，不写任何 last buffer；
- 删除 `_last_structured_output` 的生产和消费；
- `_process_action_result()` 只允许按 `result.metadata["action_id"]` 取回 payload。

如果带 action id 的 rendered result 找不到 buffer，记录明确的 contract error；不要回退到“最后一个结果”。

### 4. submit tool 直接返回 structured dict

`SubmitPoCTool.execute()` 已构造 sanitized `structured` dict。直接返回该 dict：

- 删除 `_last_submit_structured_output`；
- 删除 `_render_submit_error()` 对 global 的写入；
- agent reducer 直接消费 `ToolResult.output`；
- 保留现有 private-field sanitization 和 QitOS `_model_visible_tool_output()`。

不要再为 submit 建一套 buffer。即使模型看到紧凑 JSON，也优先保证事实正确；如确需美化，应在 QitOS model-visible renderer 处理，不能破坏 reducer payload。

### 5. 强制 submit 串行

- 从 QitOS `_CONCURRENCY_SAFE_TOOLS` 删除 `submit_poc`；
- `SubmitPoCTool.spec.concurrency_safe=False`；
- 若同一 decision 有多个 submit，executor 将它们视为 exclusive 并按原序执行；
- agent 的 prompt 不再建议 parallel submit。

READ/GREP 可以继续并行。

### 6. 删除兼容残留

全仓搜索并清零：

```text
_last_structured_output
_last_submit_structured_output
```

`_structured_output_buffer` 保留，但 key 必须是 action id，并在消费后 pop。

## 必须先写的测试

1. 两个并行 READ 故意让第二个先完成，assert 每个 result 的 action id、path、content 不串线。
2. 一个 READ 成功、一个 READ error，assert id 和 error 保持对应。
3. tool 直接单元调用且无 runtime context，assert 返回 dict，不污染共享状态。
4. 两个 submit 出现在同一 decision，使用 fake tool 记录时间/顺序，assert 不并发且按 action 顺序。
5. submit A/B 返回不同 `poc_id`/exit code，assert reducer 的 FeedbackRecord 对应正确 candidate hash。
6. buffer miss 时产生 contract error，不消费另一个 action 的 payload。
7. 现有 native tool-call history 的 tool_call_id 仍等于 action id。

## 验收不变量

- 任意 action result 都能从 metadata 找到唯一 action id；
- executor completion order 不影响 reducer attribution；
- 不存在 module/global/last payload fallback；
- submit 永远不进入 thread pool；
- private fix-side 字段仍不进入 model-visible history；
- QitOS 非 CyberGym 工具测试不回归。

## 完成后运行

```bash
PYTHONPATH=../qitos:.. python -m pytest tests/test_result_contract.py tests/test_agent.py -q
PYTHONPATH=../qitos:.. python -m pytest ../qitos/tests/test_advanced_tools_and_executor.py ../qitos/tests/test_native_tool_calling_runtime.py -q
```

暂不运行同步脚本；等任务 08 统一同步。
