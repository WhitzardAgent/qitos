# QitOS Multi-Agent Handoff 设计方案

> 版本：draft v1  
> 日期：2026-05-12  
> 目标：为 QitOS 引入多智能体 Handoff 能力，同时严格遵守 single-kernel 原则

---

## 1. 背景与动机

### 1.1 为什么现在考虑 Handoff

QitOS 路线图将 Native Multi-Agent Core 安排在 v0.7（2026-11），但核心设计决策需要提前理清：

- 当前 QitOS 的 `AgentModule + Engine` 只支持单智能体循环，一个 run 只有一个 AgentModule 实例驱动
- OpenAI Agent SDK 和 Claude Code 已经证明 handoff/delegation 是多智能体协作的基本原语
- QitOS 的 branching 机制（`Decision.branch()` + `BranchSelector`）看起来像多路径选择，但实际上只在单步内做候选决策的评分和筛选，不能跨步骤维持独立的子智能体上下文
- 如果等到 v0.7 才开始设计，前面的 harness、trace、compaction 等子系统可能需要做破坏性改动

因此现在需要确定 handoff 的核心抽象和插入点，确保后续子系统演进不会与之冲突。

### 1.2 参考实现的关键洞察

#### OpenAI Agent SDK 的做法

- **Handoff 即 Tool**：每个 handoff 目标被序列化为一个 function tool，LLM 通过调用该 tool 来触发转移
- **Runner 循环切换 agent**：当检测到 handoff tool call 时，Runner 不执行普通工具，而是切换 `current_agent`，继续同一循环
- **HandoffInputData 过滤器**：handoff 发生时，可以通过 filter 决定下一个 agent 看到多少历史消息，支持全量传递、摘要压缩、或完全隔离
- **nest_handoff_history**：将前一段对话压缩成 `<CONVERSATION_HISTORY>` 块，作为一条 assistant message 注入下一段
- **Guardrail 仅在首尾执行**：input guardrail 只在第一个 agent 运行，output guardrail 只在最后一个 agent 运行

关键限制：OpenAI 的 handoff 是**线性切换**，不是并行——一次只有一个 agent 在运行。

#### Claude Code 的做法

- **Agent 即 Tool**：`Agent` 工具是唯一的子智能体入口，同步或异步执行
- **上下文隔离**：每个子智能体有独立的 `ToolUseContext`、messages 数组、system prompt，但可以共享部分状态（如文件缓存）
- **Fork 模式**：子智能体继承父智能体的完整对话和 system prompt，利用 prompt cache 共享
- **结果回流**：同步子智能体直接返回结果；异步子智能体通过 `<task-notification>` XML 消息注入父对话
- **Coordinator 模式**：专门的编排模式，coordinator 永远异步启动 worker，结果以通知形式回传

关键优势：Claude Code 天然支持**并行**——多个子智能体可以同时运行。

### 1.3 QitOS 的独特约束

1. **Single-kernel 原则不可打破**：一次 run 只有一个 Engine 实例在驱动。handoff 不能引入第二个循环
2. **State 是单一事实来源**：一个 run 只有一个 StateSchema 实例在演进
3. **Trace 必须完整**：多智能体交互必须能被 qita 完整回放
4. **研究优先**：handoff 不仅是工程功能，还必须是可研究、可比较的研究对象

---

## 2. 核心设计决策

### 2.1 决策一：Handoff 的表现形式

**选择：Handoff 同时支持两种模式——Tool 模式和 Decision 模式**

| 模式 | 类比 | 适用场景 |
|------|------|----------|
| Tool 模式（DelegateTool） | Claude Code 的 Agent tool | 父智能体将子任务委托给子智能体，等待结果返回后继续 |
| Decision 模式（`Decision.handoff()`） | OpenAI Agent SDK 的 handoff | 父智能体将控制权转移给另一个智能体，自己退出循环 |

两种模式的区别：

- Tool 模式：子智能体的执行结果是 action_result，父智能体在 reduce 中处理
- Decision 模式：父智能体退出，子智能体接管循环，相当于 agent 切换

**为什么同时支持两种？**

- Tool 模式是更自然的"委托"，不需要改 Engine 循环——子智能体只是嵌套的 Engine.run()
- Decision 模式是更自然的"转交"，适合线性客服场景——用户不应该感知到智能体切换
- 两者共用同一套 AgentSpec 和 ContextFilter 抽象，只是执行语义不同

### 2.2 决策二：子智能体如何执行

**选择：Tool 模式用嵌套 Engine.run()，Decision 模式在同一个 Engine 循环内切换 agent**

Tool 模式的执行路径：
```
Engine.run()
  → step N: Decision.act(actions=[Action(name="delegate", args={"agent": "researcher", "task": "..."})])
  → ActionExecutor.execute("delegate")
    → DelegateTool.call()
      → 创建子 AgentModule 实例
      → Engine(sub_agent, ...).run(subtask)
      → 返回子智能体 final_result 作为 tool output
  → reduce() 把子智能体结果折叠进 state
```

Decision 模式的执行路径：
```
Engine.run()
  → step N: Decision.handoff(target="analyst", context={...})
  → Engine 检测到 handoff decision
  → 应用 ContextFilter 决定子智能体看到什么
  → 替换当前 AgentModule 为 target agent
  → 重置 step 计数（可选），继续循环
  → trace 中记录 handoff event
```

**为什么不统一成一种？**

两种模式对应不同的多智能体协作拓扑：

- Tool 模式 → 树状结构（父→子→孙），天然支持并行
- Decision 模式 → 链状结构（A→B→C），天然支持线性转移

### 2.3 决策三：上下文如何在智能体间传递

**选择：引入 `HandoffContext` 抽象，支持三种策略**

```python
class ContextStrategy(str, Enum):
    FULL = "full"           # 传递完整对话历史
    SUMMARY = "summary"     # 将历史压缩成摘要后传递
    ISOLATED = "isolated"   # 仅传递 task 描述，不传递历史
```

每种策略的具体行为：

| 策略 | Tool 模式 | Decision 模式 |
|------|-----------|---------------|
| FULL | 子智能体可以看到父对话的完整 history | 新 agent 继续使用当前 history |
| SUMMARY | 父对话被 CompactHistory 压缩后传入 | 前一段对话被压缩成一条 assistant message |
| ISOLATED | 子智能体只收到 task 字符串 | 新 agent 只收到 task + handoff context |

```python
@dataclass
class HandoffContext:
    """智能体间传递的上下文包"""
    strategy: ContextStrategy = ContextStrategy.SUMMARY
    # 传递给子智能体的额外结构化数据
    payload: Dict[str, Any] = field(default_factory=dict)
    # 父智能体希望子智能体看到的 state 字段列表
    shared_state_fields: List[str] = field(default_factory=list)
    # 最大历史轮数（FULL 模式下截断用）
    max_history_rounds: Optional[int] = None
```

### 2.4 决策四：State 在多智能体场景下的处理

**选择：Tool 模式下子智能体有独立 state；Decision 模式下智能体共享同一个 state**

Tool 模式：
- 子智能体创建自己的 StateSchema 实例
- 子智能体的 final_result 通过 `state.metadata["delegate_results"]` 回流到父 state
- 父智能体在 reduce 中决定如何整合子结果

Decision 模式：
- 新 agent 接管当前 state（因为只有一个 Engine 循环）
- 新 agent 的 `init_state` 不被调用（state 已经存在）
- 新 agent 的 `reduce` 直接在当前 state 上工作
- 如果新旧 agent 的 StateT 类型不同，需要提供 `state_adapter` 做转换

**state_adapter 机制：**

```python
class StateAdapter(ABC, Generic[SourceStateT, TargetStateT]):
    @abstractmethod
    def adapt(self, source: SourceStateT) -> TargetStateT:
        """将源 state 转换为目标 state"""
```

这是 Decision 模式最复杂的地方——如果不提供 adapter，就要求前后 agent 使用相同的 StateT。

### 2.5 决策五：Trace 如何记录多智能体交互

**选择：在现有 trace schema 中扩展，不引入第二套 trace 格式**

具体做法：

1. `manifest.json` 新增字段：
   - `agent_topology`: 智能体拓扑（哪些 agent 参与，谁委托谁）
   - `handoff_count`: handoff/delegation 次数

2. `events.jsonl` 新增事件类型：
   - `HANDOFF_INITIATED`: 记录 from_agent、to_agent、context_strategy
   - `HANDOFF_COMPLETED`: 记录 to_agent 的 final_result、步数、耗时
   - `DELEGATE_START`: 记录子智能体开始执行
   - `DELEGATE_END`: 记录子智能体执行完毕

3. Tool 模式的子智能体 trace：
   - 子智能体创建独立的 trace 目录，`run_id` 格式为 `{parent_run_id}__delegate_{agent_name}_{step}`
   - manifest 中记录 `parent_run_id` 字段
   - qita board 可以折叠/展开子智能体 trace

4. Decision 模式的 trace：
   - 仍然在同一个 trace 目录中
   - 每个 StepRecord 新增 `agent_id` 字段，标识当前是哪个 agent 在执行
   - qita 可以按 agent_id 过滤步骤

---

## 3. 核心抽象定义

### 3.1 AgentSpec

```python
@dataclass
class AgentSpec:
    """描述一个可被委托或转交的智能体"""
    name: str                              # 唯一标识，如 "researcher"、"coder"
    description: str                       # 给 LLM 看的说明，决定何时选择此 agent
    agent: AgentModule                     # AgentModule 实例
    # 以下字段有合理默认值
    context_strategy: ContextStrategy = ContextStrategy.SUMMARY
    model_override: Optional[str] = None   # 覆盖模型，不设则继承父智能体
    tools_override: Optional[ToolRegistry] = None  # 覆盖工具集
    max_steps_override: Optional[int] = None       # 覆盖步数上限
    state_adapter: Optional[StateAdapter] = None   # Decision 模式下的 state 转换器
```

### 3.2 AgentRegistry

```python
class AgentRegistry:
    """管理当前 run 可用的智能体列表"""
    def register(self, spec: AgentSpec) -> None: ...
    def resolve(self, name: str) -> AgentSpec: ...
    def get_handoff_tools(self) -> List[BaseTool]: ...  # 返回所有 handoff tool 定义
    def list_available(self) -> List[AgentSpec]: ...
```

AgentRegistry 被挂载到 Engine 构造参数中，与 ToolRegistry 同级。

### 3.3 DelegateTool（Tool 模式的核心）

```python
class DelegateTool(BaseTool):
    """将子智能体包装成工具"""
    def __init__(self, spec: AgentSpec, agent_registry: AgentRegistry): ...
    
    def call(self, args: Dict[str, Any], runtime_context: Dict[str, Any]) -> ToolResult:
        task = args.get("task", "")
        context = self._build_handoff_context(runtime_context)
        sub_engine = Engine(
            agent=self.spec.agent,
            budget=RuntimeBudget(max_steps=self.spec.max_steps_override or 10),
            ...
        )
        result = sub_engine.run(task, ...)
        return ToolResult(content=result.state.final_result or "", ...)
```

DelegateTool 是一个 BaseTool 实现，对 Engine 循环完全透明——它只是一个返回字符串结果的工具。

### 3.4 Decision.handoff()（Decision 模式的核心）

```python
# 在 Decision 中新增 handoff 工厂方法
@staticmethod
def handoff(
    target: str,
    context: Optional[HandoffContext] = None,
    rationale: Optional[str] = None,
) -> "Decision":
    return Decision(
        mode="handoff",
        actions=[],
        final_answer=None,
        rationale=rationale,
        meta={"handoff_target": target, "handoff_context": context},
    )
```

Engine 循环中新增对 `"handoff"` mode 的处理：

```python
# engine.py _run_decide 中新增
if decision.mode == "handoff":
    target_name = decision.meta["handoff_target"]
    spec = self.agent_registry.resolve(target_name)
    self._execute_handoff(spec, decision.meta.get("handoff_context"))
```

### 3.5 Handoff 工具的自动注册

当 AgentRegistry 被传入 Engine 时，Engine 自动为每个 AgentSpec 生成一个 handoff tool：

```python
# 生成的 tool schema 示例（以 researcher agent 为例）
{
    "name": "delegate_to_researcher",
    "description": "Delegate a research subtask to the researcher agent. Use this when you need to search, analyze, or investigate information.",
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The subtask to delegate"},
            "context": {"type": "object", "description": "Optional context to pass"}
        },
        "required": ["task"]
    }
}
```

这些 tool 被注入到 ToolRegistry 中，LLM 可以像调用普通工具一样触发 handoff。

---

## 4. 改造涉及的文件与模块

### 4.1 新增文件

| 文件 | 内容 |
|------|------|
| `qitos/core/agent_spec.py` | `AgentSpec`、`AgentRegistry`、`HandoffContext`、`ContextStrategy`、`StateAdapter` |
| `qitos/kit/tools/delegate.py` | `DelegateTool` 实现 |
| `qitos/engine/handoff.py` | `execute_handoff()` 逻辑、context filter/build |
| `qitos/engine/_handoff_runtime.py` | handoff 执行的内部运行时（与 `_model_runtime` 同级） |
| `examples/patterns/handoff.py` | 最小 handoff 示例 |
| `examples/patterns/delegate.py` | 最小 delegate 示例 |

### 4.2 需修改的文件

| 文件 | 修改内容 |
|------|----------|
| `qitos/core/decision.py` | 新增 `DecisionMode` 值 `"handoff"`，新增 `Decision.handoff()` 工厂方法 |
| `qitos/core/decision.py` | `validate()` 方法中接受 `"handoff"` mode |
| `qitos/engine/engine.py` | 构造函数新增 `agent_registry` 参数；`run()` 循环中处理 handoff mode |
| `qitos/engine/_model_runtime.py` | `run_decide()` 中处理 handoff decision |
| `qitos/engine/_action_runtime.py` | 识别 DelegateTool 调用，注入子 trace context |
| `qitos/engine/action_executor.py` | `runtime_context` 新增 `agent_registry`、`parent_run_id` |
| `qitos/engine/states.py` | `StepRecord` 新增 `agent_id` 字段 |
| `qitos/trace/events.py` | 新增 `HANDOFF_INITIATED`、`HANDOFF_COMPLETED`、`DELEGATE_START`、`DELEGATE_END` 事件类型 |
| `qitos/trace/schema.py` | manifest schema 新增 `agent_topology`、`handoff_count`、`parent_run_id` |
| `qitos/harness/_presets.py` | AgentSpec 可选关联 FamilyPreset |

### 4.3 不需要修改的文件

以下子系统无需改动即可兼容 handoff：

- **Parser 系统**：handoff tool 和普通 tool 一样被 parser 解析
- **Critic 系统**：critic 在 handoff 前后正常工作
- **StopCriteria**：FinalResultCriteria 仍然有效
- **History / Compaction**：HandoffContext 策略决定如何处理 history，不需要改 CompactHistory 本身
- **Memory**：子智能体可以选择共享或独立使用 Memory
- **Env**：环境与智能体无关

---

## 5. 分阶段实施计划

### Phase 1：Tool 模式（DelegateTool）— 最小可用

**目标**：在不改 Engine 循环的前提下，通过 DelegateTool 实现委托式多智能体

**具体步骤**：

1. 实现 `AgentSpec`、`AgentRegistry`、`HandoffContext`
2. 实现 `DelegateTool(BaseTool)`——内部创建嵌套 Engine.run()
3. 在 AgentModule 构造函数中新增可选的 `agent_registry` 参数
4. 让 `DelegateTool` 自动注册为 tool（AgentRegistry.get_handoff_tools()）
5. 在 trace 中记录 DELEGATE_START / DELEGATE_END 事件
6. 编写 `examples/patterns/delegate.py` 示例

**不改动的部分**：
- Decision 不变
- Engine 循环不变
- Parser 不变

**验证标准**：
- 一个 coding agent 可以通过 DelegateTool 委托搜索任务给 researcher agent
- 子智能体的 trace 被写入独立子目录
- qita board 能看到子智能体的 run

### Phase 2：Decision 模式（Decision.handoff()）— 线性转交

**目标**：在 Engine 循环内支持智能体切换

**具体步骤**：

1. 在 `DecisionMode` 中新增 `"handoff"`
2. 实现 `Decision.handoff()` 工厂方法
3. 在 Engine.run() 循环中增加 handoff mode 的处理分支
4. 实现 `_handoff_runtime.py`——处理 agent 切换、context filter、state adapter
5. StepRecord 新增 `agent_id`
6. trace 新增 HANDOFF_INITIATED / HANDOFF_COMPLETED 事件
7. 编写 `examples/patterns/handoff.py` 示例

**验证标准**：
- 一个 triage agent 可以 handoff 给 refund agent，refund agent 继续在同一循环中运行
- qita 能按 agent_id 过滤步骤
- trace 中的 handoff 事件完整记录

### Phase 3：上下文策略与 Shared Memory

**目标**：完善智能体间的上下文传递

**具体步骤**：

1. 实现 ContextStrategy.FULL：传递完整 history
2. 实现 ContextStrategy.SUMMARY：用 CompactHistory 压缩历史
3. 实现 ContextStrategy.ISOLATED：只传递 task
4. 实现 StateAdapter 抽象和默认实现
5. 支持共享 Memory 实例（多个 agent 读写同一个 VectorMemory）
6. 编写"共享 vs 隔离 memory"指南

**验证标准**：
- 三种 context 策略都有可运行示例
- shared memory 场景下两个 agent 能读写同一份 memory
- isolation 场景下子 agent 完全看不到父 history

### Phase 4：规范模式模板

**目标**：提供开箱即用的多智能体协作模板

**具体步骤**：

1. 实现 manager-worker 模板
2. 实现 planner-executor 模板
3. 实现 proposer-verifier 模板
4. 在 `templates/` 下新增 multi-agent 模板
5. 编写"构建第一个多智能体系统"教程
6. qita 增加 handoff timeline 视图

---

## 6. 与路线图的对齐

本方案与 QitOS 路线图的对应关系：

| 路线图阶段 | 本方案对应 |
|-----------|-----------|
| v0.5（当前） | Phase 1 研究 + 设计确认 |
| v0.6 | Phase 1 实现（DelegateTool） |
| v0.7 | Phase 2 + Phase 3 + Phase 4 |

**关键约束**：Phase 1（DelegateTool）必须可在 v0.6 之前完成，因为它不改 Engine 循环，风险极低。Phase 2 的 Decision.handoff() 是更重的改动，应该放在 v0.7 周期。

---

## 7. 风险与缓解

### 风险 1：嵌套 Engine.run() 的递归深度

DelegateTool 内部调用 Engine.run()，如果子智能体再次调用 DelegateTool，会产生递归。

**缓解**：在 runtime_context 中维护 `delegate_depth` 计数器，超过阈值时拒绝新的 delegate。

### 风险 2：Decision 模式下 State 类型不兼容

如果前后 agent 的 StateT 类型不同，handoff 会崩溃。

**缓解**：要求 Decision 模式下要么前后 agent 使用同一 StateT，要么提供 StateAdapter。无 adapter 时做类型检查，不匹配则报错而非静默失败。

### 风险 3：Trace 目录膨胀

Tool 模式下每个子智能体产生独立 trace 目录，深层次委托可能导致目录爆炸。

**缓解**：
- 限制最大委托深度（默认 3）
- 提供 `trace=TraceWriter.INHERIT` 选项，让子智能体将事件写入父 trace（带 agent_id 标记）

### 风险 4：与 Harness 系统的交互

不同智能体可能需要不同的 FamilyPreset。

**缓解**：AgentSpec.model_override 和 tools_override 已经覆盖了常见需求。如果需要完整的 harness 切换，在 AgentSpec 中新增 `harness_override: Optional[HarnessPolicy]`。

---

## 8. 未决定的问题

以下问题需要在实现前进一步讨论：

1. **Decision 模式是否需要支持"回交"（handoff back）？** OpenAI SDK 不支持——handoff 是单向的。如果支持，需要额外的 `HandoffBack` 机制
2. **Tool 模式下子智能体是否应该共享父的 Env？** 如果共享，子智能体的环境操作会直接影响父；如果隔离，子智能体需要自己的 Env 实例
3. **qita 如何可视化多智能体 trace？** 时间线视图 vs 树状视图 vs 嵌套折叠视图，需要做 UX 原型
4. **是否需要支持异步 delegate？** Claude Code 支持后台子智能体，但 QitOS 当前是同步循环。异步需要引入事件循环，改动较大
5. **Handoff 的 LLM 提示如何设计？** 需要设计 system prompt 中关于可用 agent 的描述格式，确保 LLM 能正确选择 handoff 目标

---

## 9. 总结

本方案的核心设计思路：

1. **两种 handoff 模式并行**：Tool 模式（委托）和 Decision 模式（转交），覆盖树状和链状两种协作拓扑
2. **渐进式改造**：Phase 1 不改 Engine 循环，Phase 2 才涉及循环扩展，降低风险
3. **遵守 single-kernel**：Tool 模式用嵌套 Engine（不引入第二循环），Decision 模式在同一循环内切换 agent
4. **Context 可控**：三种上下文策略让研究者可以精确控制智能体间的信息流
5. **Trace 完整**：多智能体交互完整记录在 trace 中，支持 qita 回放和分析

这组设计使得 QitOS 可以在不牺牲 single-kernel 原则和 trace-first 设计的前提下，系统性地支持多智能体研究。
