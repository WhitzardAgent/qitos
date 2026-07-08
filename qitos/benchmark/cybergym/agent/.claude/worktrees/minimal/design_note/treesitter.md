# Tree-sitter Based C/C++ 跨函数分析与 Agent 运行时集成规格

## 1. 任务目标

在现有 Tree-sitter 路径约束提取能力基础上，将分析器扩展为一个面向 C/C++ 代码库的轻量跨函数分析模块，为 Agent 提供：

1. 从指定目标函数、调用点或 sink 出发的候选调用链；
2. 调用链上每一层函数调用的实际参数与形式参数映射；
3. 每个调用点必须满足的局部控制流约束；
4. 跨函数组合后的 entrypoint-to-target 路径约束；
5. sink 参数的局部反向数据流；
6. 所有分析结论对应的源码位置、证据、置信度和未解析项。

本模块不追求编译器级精确分析，也不要求完整覆盖复杂 C++ 语义。目标是构建一个：

> 高召回、可解释、可缓存、可增量更新，并能够以“自动 Sink Enrichment + Agent 按需查询”双层模式运行的跨函数代码分析能力。

该模块不是一个只能由 Agent 主动调用的独立工具。它需要同时具备：

1. **自动增强模式**：Agent 提出 sink candidate、vulnerable function 或目标代码位置后，由 harness 自动触发轻量分析，并将压缩后的分析摘要注入 Agent 上下文；
2. **按需查询模式**：Agent 根据自动摘要中的候选路径、约束和 unresolved 项，进一步调用细粒度工具展开分析。

---

## 2. 最终能力形态

系统需要支持以下分析流程：

```text
Repository
  -> Tree-sitter 解析
  -> 全仓库函数与调用点索引
  -> 单函数控制流与局部数据流摘要
  -> 候选调用图
  -> 调用点参数绑定
  -> 跨函数约束组合
  -> sink 参数反向切片
  -> Runtime Analysis Service
       ├── 自动 Sink Enrichment
       │    └── 压缩 Sink Analysis Brief 注入 Agent 上下文
       └── Agent 按需查询工具
            ├── 展开候选路径
            ├── 深化路径约束
            ├── 追踪 sink 参数
            └── 分析 unresolved 调用点
```

最终需要能够对如下代码恢复跨函数路径：

```c
void handle(Request *req) {
    if (req == NULL)
        return;

    parse(req->body, req->body_len);
}

void parse(char *buf, size_t len) {
    if (len <= 1024)
        return;

    copy_payload(buf, len);
}

void copy_payload(char *src, size_t size) {
    memcpy(local_buffer, src, size);
}
```

输出：

```text
handle(req)
  [req != NULL]
  -> parse(req->body, req->body_len)

parse(buf, len)
  [len > 1024]
  -> copy_payload(buf, len)

copy_payload(src, size)
  -> memcpy(local_buffer, src, size)
```

并进一步组合为：

```text
entrypoint:
  handle

call_chain:
  handle
  -> parse
  -> copy_payload
  -> memcpy

constraints:
  req != NULL
  req->body_len > 1024

bindings:
  parse.buf  := req->body
  parse.len  := req->body_len
  copy_payload.src  := req->body
  copy_payload.size := req->body_len

sink_dataflow:
  memcpy.src  <- req->body
  memcpy.size <- req->body_len
```

---

## 3. 明确的能力边界

### 3.1 必须实现

第一版必须实现：

- C/C++ 源文件扫描；
- 函数定义索引；
- 调用点索引；
- 同文件直接调用解析；
- 跨文件同名函数候选解析；
- `static` 函数作用域隔离；
- 普通成员调用候选解析；
- 简单函数指针赋值和静态初始化表；
- 形式参数与实际参数绑定；
- `if/else` 控制条件；
- early-return 条件反转；
- `switch/case/default` 条件；
- `&&`、`||`、`!` 条件；
- 简单循环上下文；
- 局部变量定义与使用关系；
- sink 参数局部反向切片；
- 多层函数调用路径组合；
- 所有结果携带源码位置；
- 所有调用边携带置信度；
- 无法解析的结果显式标记为 unresolved；
- Agent sink candidate 的结构化提交协议；
- sink candidate 提交后的自动分析触发器；
- 压缩的 Sink Analysis Brief；
- 自动分析结果的上下文注入协议；
- 自动分析的 token、路径数、深度和数据流步数预算；
- 相同 sink candidate 的去重、缓存和版本更新；
- 自动摘要不足时的 Agent 显式下钻工具。

### 3.2 不要求实现

第一版不要求：

- 精确虚函数派发；
- 完整模板实例化分析；
- 完整宏展开语义；
- 完整指针别名分析；
- 完整堆对象追踪；
- 完整 SSA；
- 全程序符号执行；
- 并发与线程间数据流；
- 自动证明路径一定可达；
- 自动生成最终 PoC。

遇到上述场景时，必须返回候选集合或 unresolved，禁止伪造唯一结论。

---

## 4. 统一 Analysis IR

所有模块必须使用统一的中间表示，不允许在模块间只传递源码字符串或 Tree-sitter Node。

### 4.1 SourceLocation

```python
@dataclass
class SourceLocation:
    file: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
```

### 4.2 ExprIR

```python
@dataclass
class ExprIR:
    kind: str
    value: object | None
    children: list["ExprIR"]
    source_text: str
    location: SourceLocation | None
```

必须支持以下 `kind`：

```text
identifier
constant
null
field_access
pointer_field_access
array_access
call
unary
binary
cast
address_of
dereference
conditional
sizeof
unknown
```

例如：

```c
packet->length > 256
```

表示为：

```json
{
  "kind": "binary",
  "value": ">",
  "children": [
    {
      "kind": "pointer_field_access",
      "value": "length",
      "children": [
        {
          "kind": "identifier",
          "value": "packet"
        }
      ]
    },
    {
      "kind": "constant",
      "value": 256
    }
  ],
  "source_text": "packet->length > 256"
}
```

### 4.3 FunctionSymbol

```python
@dataclass
class FunctionSymbol:
    symbol_id: str
    name: str
    qualified_name: str
    file: str
    scope: str | None
    parameters: list["Parameter"]
    is_static: bool
    language: str
    body_location: SourceLocation
```

### 4.4 CallSite

```python
@dataclass
class CallSite:
    callsite_id: str
    caller_id: str
    callee_text: str
    receiver: ExprIR | None
    arguments: list[ExprIR]
    location: SourceLocation
    local_guards: list["ConstraintIR"]
    candidates: list["CallCandidate"]
```

### 4.5 CallCandidate

```python
@dataclass
class CallCandidate:
    symbol_id: str
    resolution_kind: str
    confidence: float
    evidence: list[str]
```

### 4.6 ConstraintIR

```python
@dataclass
class ConstraintIR:
    expression: ExprIR
    source_text: str
    normalized_text: str
    polarity: bool
    origin_function: str
    origin_location: SourceLocation
    reason: str
    confidence: float
```

`reason` 至少支持：

```text
if_true_branch
if_false_branch
early_return_bypass
switch_case
switch_default
loop_entry
short_circuit
opaque_predicate
```

### 4.7 CallEdge

```python
@dataclass
class CallEdge:
    caller_id: str
    callee_id: str
    callsite_id: str
    bindings: dict[str, ExprIR]
    guards: list[ConstraintIR]
    resolution_kind: str
    confidence: float
```

---

## 5. Repository Index

### 5.1 文件扫描

默认扫描：

```text
.c
.h
.cc
.cpp
.cxx
.hh
.hpp
.hxx
```

需要支持：

- 排除构建目录；
- 排除第三方依赖目录；
- 排除超过大小限制的文件；
- 使用文件内容哈希做增量更新；
- 同一文件未修改时复用缓存。

默认排除目录：

```text
.git
build
dist
out
vendor
third_party
node_modules
target
```

排除规则必须可配置。

### 5.2 函数定义索引

需要提取：

- 函数名；
- 限定名；
- 所属 namespace；
- 所属 class/struct；
- 参数列表；
- 参数数量；
- 是否 `static`；
- 声明和定义位置；
- 函数体范围；
- 所属文件。

建议 symbol ID：

C 普通函数：

```text
<relative_file>::<function_name>/<arity>
```

C `static` 函数：

```text
<relative_file>::static::<function_name>/<arity>
```

C++ 方法：

```text
<relative_file>::<namespace>::<class>::<method>/<arity>
```

### 5.3 调用点索引

必须识别：

```c
foo(a, b);
ns::foo(a);
object.method(a);
object->method(a);
callback(a);
ops->handler(a);
table[type](a);
```

每个调用点必须保存：

- caller；
- callee 原始文本；
- receiver；
- 参数列表；
- 参数数量；
- 源码位置；
- 所在函数；
- 调用点局部控制条件；
- 候选 callee；
- 解析置信度。

---

## 6. 单函数控制流摘要

### 6.1 目标

给定函数内任意调用点或 sink，提取从函数入口到该位置需要满足的控制条件。

### 6.2 必须支持的控制结构

- `if`;
- `if/else`;
- nested `if`;
- early return;
- `switch`;
- `case`;
- `default`;
- case fallthrough;
- `break`;
- `continue`;
- `goto`;
- `for`;
- `while`;
- `do while`;
- `&&`;
- `||`;
- `!`;
- conditional expression。

### 6.3 Early-return 处理

代码：

```c
if (length <= 256)
    return;

sink(data, length);
```

必须输出：

```text
length > 256
```

不能输出：

```text
length <= 256
```

### 6.4 Switch 处理

代码：

```c
switch (packet->type) {
case TYPE_A:
case TYPE_B:
    sink(packet);
    break;
default:
    return;
}
```

输出：

```text
packet->type == TYPE_A
OR packet->type == TYPE_B
```

### 6.5 短路逻辑

代码：

```c
if (ptr && ptr->length > 1024)
    sink(ptr);
```

输出：

```text
ptr != NULL
AND ptr->length > 1024
```

### 6.6 循环处理

第一版不展开循环。

代码：

```c
for (size_t i = 0; i < count; i++) {
    if (items[i].type == TARGET)
        sink(&items[i]);
}
```

输出：

```json
{
  "loop_condition": "i < count",
  "body_condition": "items[i].type == TARGET",
  "may_execute_zero_times": true,
  "iteration_count": "unknown"
}
```

---

## 7. 函数摘要

每个函数必须缓存一个可复用摘要。

```python
@dataclass
class FunctionSummary:
    function_id: str
    parameters: list[str]
    calls: list[CallSite]
    returns: list["ReturnSummary"]
    local_definitions: list["DefinitionIR"]
    field_writes: list["FieldWriteIR"]
    early_exits: list["EarlyExitIR"]
    unresolved_nodes: list["UnresolvedIR"]
```

摘要至少包含：

- 参数；
- 直接调用；
- 每个调用点的局部 guard；
- 返回表达式；
- 局部变量定义；
- 结构体字段写入；
- early exit；
- opaque predicate；
- 未解析语法节点。

跨函数分析必须优先组合函数摘要，不得在每次查询时重新完整遍历函数 AST。

---

## 8. 候选调用解析

### 8.1 解析状态

每个调用点必须属于以下三种状态之一：

```text
resolved_unique
resolved_candidates
unresolved
```

### 8.2 直接调用

优先级：

1. 同文件唯一 `static` 函数；
2. 同作用域唯一函数；
3. 显式限定名匹配；
4. 同名且参数数量匹配；
5. 跨文件同名候选集合。

### 8.3 普通成员调用

代码：

```cpp
Parser parser;
parser.parse(input);
```

在局部变量存在显式类型声明时，可以生成：

```text
Parser::parse
```

代码：

```cpp
auto parser = create_parser();
parser.parse(input);
```

无法确定时返回 unresolved 或候选集合。

### 8.4 简单函数指针

第一版支持：

```c
callback = handle_data;
callback(packet);
```

```c
struct Ops ops = {
    .parse = parse_packet
};

ops.parse(packet);
```

```c
Handler table[] = {
    handle_a,
    handle_b
};

table[type](packet);
```

结果示例：

```json
{
  "resolution_kind": "function_pointer_table",
  "candidate_targets": [
    "handle_a",
    "handle_b"
  ],
  "selection_expression": "type",
  "confidence": 0.6
}
```

### 8.5 回调注册模式

支持配置：

```yaml
registration_patterns:
  - function: register_handler
    key_argument: 0
    callback_argument: 1
```

代码：

```c
register_handler(TYPE_DATA, handle_data);
```

生成：

```text
TYPE_DATA -> handle_data
```

### 8.6 置信度

建议默认值：

```text
1.00  同文件唯一 static 函数
0.95  显式限定名唯一匹配
0.90  同作用域唯一匹配
0.85  跨文件唯一名称与参数数量匹配
0.70  局部显式类型推断成员方法
0.60  静态函数指针初始化
0.45  多候选名称匹配
0.20  仅文本相同
```

---

## 9. 候选调用图

调用图使用有向图：

```text
caller -> callee
```

每条边必须保存：

- callsite；
- actual arguments；
- formal parameters；
- bindings；
- local guards；
- resolution kind；
- confidence；
- source evidence。

必须支持：

```python
find_callers(symbol, max_depth, top_k)
find_callees(symbol, max_depth, top_k)
find_paths(source, target, max_depth, top_k)
find_paths_to_target(target, entrypoint_patterns, max_depth, top_k)
```

### 9.1 路径搜索限制

必须支持：

- 最大调用深度；
- Top-K；
- 最低边置信度；
- 每个调用点最大候选数；
- 递归环检测；
- 同一函数重复访问限制；
- SCC 折叠；
- 路径超限时返回 partial。

### 9.2 路径评分

建议：

```text
path_score =
  product(edge_confidence)
  * depth_penalty
  * unresolved_penalty
```

优先返回：

- 更短；
- 直接调用更多；
- unresolved 更少；
- 调用点证据更充分的路径。

---

## 10. 参数绑定与表达式替换

### 10.1 参数绑定

代码：

```c
parse(req->body + offset, req->length - offset);
```

callee：

```c
void parse(char *buf, size_t len);
```

绑定：

```text
buf := req->body + offset
len := req->length - offset
```

### 10.2 表达式替换

callee 条件：

```text
len > 256
```

替换后：

```text
req->length - offset > 256
```

### 10.3 实现要求

- 不允许使用字符串 `replace`；
- 必须在 ExprIR 上做结构化替换；
- 必须处理同名变量；
- 必须保留源码位置；
- 必须记录替换链；
- 无法替换的局部变量进入 unresolved。

### 10.4 作用域命名

内部变量建议使用：

```text
<function_id>::<variable_name>
```

避免不同函数中同名变量冲突。

---

## 11. 跨函数路径约束组合

沿调用链组合每一层调用点 guard。

示例：

```text
handle:
  req != NULL
  req->method == POST

parse:
  packet != NULL
  packet->type == TYPE_DATA

copy:
  packet->length > 256
```

经过参数绑定后输出：

```text
req != NULL
AND req->method == POST
AND decode(req->body) != NULL
AND decode(req->body)->type == TYPE_DATA
AND decode(req->body)->length > 256
```

### 11.1 约束处理步骤

1. 提取 caller 到 callsite 的局部 guard；
2. 获取 callee 内下一个 callsite 的局部 guard；
3. 进行 formal-to-actual 替换；
4. 合并约束；
5. 归一化；
6. 去重；
7. 检测直接矛盾；
8. 保留 opaque predicate；
9. 记录每条约束来源。

### 11.2 基础归一化

必须支持：

```text
!(x == y)   -> x != y
!(x != y)   -> x == y
!(x <= y)   -> x > y
!(x < y)    -> x >= y
!(x >= y)   -> x < y
!(x > y)    -> x <= y
!!x         -> x
x && true   -> x
x || false  -> x
x != NULL   -> non_null(x)
```

### 11.3 多路径

不要过早将所有路径合并。

代码：

```c
if (mode == A)
    sink(data);
else if (mode == B)
    sink(data);
```

保留：

```text
Path 1:
  mode == A

Path 2:
  mode != A
  AND mode == B
```

---

## 12. 局部 Def-Use 与反向切片

### 12.1 目标

从 sink 参数向后追踪：

- 局部变量；
- 函数参数；
- 结构体字段；
- 全局变量；
- 常量；
- 函数返回值；
- 未知内存来源。

### 12.2 第一版支持

- 变量声明；
- 简单赋值；
- compound assignment；
- 参数；
- 字段访问；
- pointer field access；
- 数组访问；
- cast；
- 算术表达式；
- 条件赋值；
- 简单 return summary。

### 12.3 示例

```c
size_t n = packet->declared_length;
size_t copy_size = n + HEADER_SIZE;
memcpy(dst, packet->payload, copy_size);
```

输出：

```text
memcpy.size
<- copy_size
<- n + HEADER_SIZE
<- packet->declared_length + HEADER_SIZE
```

### 12.4 Phi 合并

代码：

```c
if (flag)
    size = a;
else
    size = b;
```

表示为：

```json
{
  "kind": "phi",
  "alternatives": [
    {
      "guard": "flag",
      "value": "a"
    },
    {
      "guard": "!flag",
      "value": "b"
    }
  ]
}
```

### 12.5 分析状态

每个 sink 参数必须返回：

```text
resolved
partially_resolved
unresolved
```

禁止因为追踪中断而直接判断 attacker-controlled。

---


## 13. Agent 运行时定位：自动 Sink Enrichment + 按需查询

### 13.1 设计结论

本能力必须实现为一个由 harness 管理的分析服务，同时向 Agent 暴露查询工具。

禁止采用以下两种单一模式：

1. **仅显式工具模式**：完全依赖 Agent 主动意识到需要跨函数分析；
2. **全量自动注入模式**：发现任意危险函数名后自动展开完整调用图并塞入上下文。

正确模式是：

```text
Agent 提出 sink candidate
        |
        v
Harness 自动触发受控轻量分析
        |
        v
生成 Sink Analysis Brief
        |
        v
作为独立 observation 注入 Agent 上下文
        |
        v
Agent 根据 brief 决定是否调用细粒度分析工具
```

自动阶段负责提供“路径地图”，显式工具负责沿某一条路径深入。

### 13.2 自动触发对象

自动分析只针对 Agent 或上游任务状态明确提交的目标，不得因为仓库中出现普通危险函数名就对全仓库自动分析。

支持以下触发对象：

```text
sink_candidate
vulnerable_function_candidate
target_callsite
target_source_location
patch_related_location
```

优先触发条件：

1. Agent 明确提交 sink candidate；
2. Agent 将某个函数标记为 vulnerable function；
3. Agent 引用 CVE、补丁或漏洞描述中的目标代码位置；
4. Agent 即将进入 PoC 构造阶段，但状态中尚无 entrypoint-to-target 路径；
5. Agent 更新已有 candidate 的位置、理由或置信度。

不得仅因发现 `memcpy`、`strcpy`、数组访问或指针解引用而自动触发。

### 13.3 SinkCandidate 协议

Agent 或 harness 必须通过结构化对象提交候选目标：

```python
@dataclass
class SinkCandidate:
    candidate_id: str
    repository_id: str
    file: str
    line: int
    function: str | None
    callee: str | None
    expression: str | None
    category: str | None
    reason: str
    agent_confidence: float
    evidence_locations: list[SourceLocation]
    related_cve: str | None
    metadata: dict[str, object]
```

示例：

```json
{
  "candidate_id": "sink_00017",
  "repository_id": "repo_current",
  "file": "src/parser.c",
  "line": 247,
  "function": "copy_payload",
  "callee": "memcpy",
  "category": "memory_copy",
  "reason": "CVE patch indicates unchecked packet length",
  "agent_confidence": 0.82,
  "evidence_locations": [
    {
      "file": "src/parser.c",
      "start_line": 240,
      "start_column": 1,
      "end_line": 250,
      "end_column": 1
    }
  ],
  "related_cve": "CVE-XXXX-YYYY"
}
```

`reason` 是必填字段。分析器需要保留 Agent 的判断依据，但不能将其当作静态分析事实。

### 13.4 自动分析流水线

收到 `SinkCandidate` 后，运行时服务执行：

```text
1. 验证目标文件、函数和源码位置
2. 定位目标调用点或表达式
3. 获取目标函数摘要
4. 从目标反向检索 Top-K candidate caller paths
5. 对每条路径组合调用点参数绑定
6. 提取路径上的关键控制条件
7. 对关键 sink 参数执行短距离 backward slice
8. 汇总 unresolved 调用点和 opaque predicates
9. 生成受预算限制的 Sink Analysis Brief
10. 写入缓存并注入 Agent 上下文
```

自动分析必须以已有仓库索引和函数摘要为基础，不得每次重新全仓库扫描。

### 13.5 自动分析预算

默认配置：

```yaml
automatic_sink_enrichment:
  enabled: true
  top_paths: 3
  max_call_depth: 6
  max_constraints_per_path: 12
  max_dataflow_steps_per_argument: 8
  max_unresolved_items: 8
  include_source_snippets: false
  include_full_expr_ir: false
  context_token_budget: 1500
  timeout_seconds: 10
  minimum_candidate_confidence: 0.30
```

自动模式应优先保留：

1. 调用链；
2. 参数绑定；
3. 对到达目标最关键的 guard；
4. sink 的关键参数来源；
5. unresolved 项；
6. 每条路径的综合置信度。

应优先删除：

- 完整 AST；
- 大段源码；
- 重复约束；
- 与目标参数无关的局部变量；
- 超过 Top-K 的低置信度路径；
- 冗长的内部调试信息。

### 13.6 SinkAnalysisBrief

自动分析必须生成独立于完整分析结果的压缩结构：

```python
@dataclass
class SinkAnalysisBrief:
    brief_id: str
    candidate_id: str
    status: str
    target: dict[str, object]
    candidate_paths: list["BriefPath"]
    key_constraints: list["BriefConstraint"]
    argument_provenance: list["BriefProvenance"]
    unresolved: list["BriefUnresolved"]
    suggested_queries: list["SuggestedQuery"]
    confidence: dict[str, float]
    truncation: dict[str, object]
```

示例：

```yaml
brief_id: brief_00017_v1
candidate_id: sink_00017
status: partial

target:
  expression: memcpy(local, packet->payload, packet->length)
  location: src/parser.c:247

candidate_paths:
  - path_id: path_00031
    chain:
      - LLVMFuzzerTestOneInput
      - parse_packet
      - decode_record
      - copy_payload
      - memcpy
    confidence: 0.87

key_constraints:
  - size >= 8
  - record_type == TYPE_DATA
  - packet != NULL
  - packet->length > 256

argument_provenance:
  - sink_argument: memcpy.src
    expression: packet->payload
    status: partially_resolved
  - sink_argument: memcpy.size
    expression: packet->length
    status: partially_resolved

unresolved:
  - id: unresolved_call_004
    expression: ops->decode(packet)
    reason: multiple_function_pointer_candidates
  - id: opaque_predicate_008
    expression: validate_checksum(packet)
    reason: opaque_predicate

suggested_queries:
  - tool: get_path_details
    arguments:
      path_id: path_00031
  - tool: trace_value
    arguments:
      function: copy_payload
      line: 247
      expression: packet->length
```

### 13.7 上下文注入格式

自动结果必须作为独立 observation 注入，不得与 Agent 自己的推理文本混合，也不得伪装成确定事实。

推荐格式：

```xml
<static_analysis_result
  type="sink_candidate_enrichment"
  brief_id="brief_00017_v1"
  candidate_id="sink_00017"
  status="partial">

  <confirmed>
    ...
  </confirmed>

  <candidates>
    ...
  </candidates>

  <inferences>
    ...
  </inferences>

  <unresolved>
    ...
  </unresolved>

  <suggested_queries>
    ...
  </suggested_queries>
</static_analysis_result>
```

必须区分：

```text
confirmed
candidate
inferred
unresolved
```

含义：

- `confirmed`：由语法结构和索引直接支持；
- `candidate`：存在多个可能调用目标或路径；
- `inferred`：由参数替换、摘要组合或局部数据流推断；
- `unresolved`：当前轻量能力无法可靠完成。

### 13.8 自动注入生命周期

每个 brief 必须具有版本号和稳定 ID：

```text
brief_00017_v1
brief_00017_v2
```

当出现以下情况时允许更新：

- candidate 的文件或行号变化；
- Agent 提供新证据；
- 仓库文件发生变化；
- unresolved 调用点被进一步解析；
- Agent 请求更高深度重新分析。

默认只向上下文注入最新版本。旧版本保留在内部状态中，不重复占用上下文。

### 13.9 去重与抑制

对 sink candidate 计算稳定指纹：

```text
repository_id
+ normalized_file
+ line
+ function
+ normalized_expression
+ analysis_configuration_hash
```

相同指纹且分析配置未变化时：

- 复用缓存；
- 不重复注入相同 brief；
- 仅当结果发生实质变化时生成新版本。

如果同一函数中多个候选指向同一目标调用点，应合并候选理由和证据，不重复运行。

### 13.10 自动分析与显式工具的分工

自动分析只回答：

```text
可能从哪里到达目标？
每层传递了什么参数？
最关键的路径条件是什么？
sink 参数目前能追踪到哪里？
还有哪些关键节点未解析？
```

显式工具负责：

```text
展开更多候选路径
查看完整源码证据
深化某一条路径
增加最大调用深度
追踪特定 sink 参数
分析特定 unresolved 调用点
重新提取某个调用点的路径约束
```

自动分析不得默认返回完整源码、所有路径或完整表达式 IR。

### 13.11 Harness 集成状态机

推荐维护以下状态：

```text
NO_TARGET
  -> TARGET_PROPOSED
  -> AUTO_ANALYSIS_RUNNING
  -> BRIEF_AVAILABLE
  -> AGENT_REVIEWING
  -> TOOL_DRILL_DOWN
  -> TARGET_REFINED
  -> READY_FOR_NEXT_STAGE
```

状态字段至少包括：

```json
{
  "active_sink_candidate_id": "sink_00017",
  "latest_brief_id": "brief_00017_v2",
  "selected_path_id": "path_00031",
  "open_unresolved_ids": [
    "unresolved_call_004"
  ],
  "analysis_status": "partial"
}
```

### 13.12 自动模式成功标准

自动 Sink Enrichment 必须满足：

- Agent 提交有效 candidate 后自动触发；
- 不依赖 Agent 再次显式请求基础调用链；
- 默认摘要不超过配置的 context budget；
- 摘要至少返回一条候选路径或明确说明未找到；
- 相同 candidate 不重复注入相同结果；
- 所有不确定结论被正确分类；
- 摘要中包含可直接调用的下一步查询建议；
- Agent 可以通过 brief 中的 ID 继续下钻。

---

## 14. Agent 工具接口

所有工具返回稳定 JSON，不直接暴露 Tree-sitter Node。自动 Sink Enrichment 使用同一套底层分析能力，但必须通过受预算限制的服务接口调用。

### 14.1 analyze_sink_candidate

该接口是自动触发器和 Agent 手动重新分析共用的入口。

输入：

```json
{
  "candidate": {
    "candidate_id": "sink_00017",
    "repository_id": "repo_current",
    "file": "src/parser.c",
    "line": 247,
    "function": "copy_payload",
    "callee": "memcpy",
    "reason": "unchecked packet length",
    "agent_confidence": 0.82
  },
  "mode": "automatic",
  "budget_profile": "default"
}
```

`mode` 支持：

```text
automatic
interactive
deep
```

输出：

```json
{
  "brief_id": "brief_00017_v1",
  "candidate_id": "sink_00017",
  "status": "partial",
  "context_payload": "<static_analysis_result>...</static_analysis_result>",
  "full_result_id": "analysis_00017_v1",
  "cache_hit": false
}
```

### 14.2 index_repository

输入：

```json
{
  "repository": "/workspace/project",
  "languages": ["c", "cpp"]
}
```

输出：

```json
{
  "files_indexed": 1284,
  "functions": 9431,
  "callsites": 28741,
  "unresolved_callsites": 1820,
  "cache_hits": 1170,
  "status": "success"
}
```

### 14.3 find_callers

输入：

```json
{
  "symbol": "copy_payload",
  "max_depth": 5,
  "top_k": 10
}
```

### 14.4 find_paths_to_target

输入：

```json
{
  "target": "src/parser.c::copy_payload/2",
  "entrypoint_patterns": [
    "LLVMFuzzerTestOneInput",
    "fuzz_*",
    "parse_*",
    "handle_*"
  ],
  "max_depth": 8,
  "top_k": 10
}
```

### 14.5 summarize_function

输入：

```json
{
  "symbol_id": "src/parser.c::parse_packet/2"
}
```

### 14.6 extract_constraints

输入：

```json
{
  "function": "src/parser.c::parse_packet/2",
  "target_line": 247,
  "max_paths": 8
}
```

### 14.7 trace_value

输入：

```json
{
  "function": "src/parser.c::copy_payload/1",
  "line": 247,
  "expression": "copy_size",
  "direction": "backward"
}
```

### 14.8 get_path_details

输入：

```json
{
  "path_id": "path_00017",
  "include_source_evidence": true,
  "include_full_bindings": true,
  "include_all_constraints": true
}
```

输出需要包含完整调用边、参数绑定、约束来源和 unresolved 节点。

### 14.9 explain_path

输入：

```json
{
  "path_id": "path_00017",
  "format": "compact_text"
}
```

输出：

```text
LLVMFuzzerTestOneInput(data, size)
  [size >= 8]
  -> parse_packet(data + 4, size - 4)

parse_packet(buf, len)
  [len > HEADER_SIZE]
  [packet->type == TYPE_DATA]
  -> copy_payload(packet)

copy_payload(packet)
  [packet->length > 256]
  -> memcpy(local, packet->payload, packet->length)
```

### 14.10 resolve_callsite_candidates

用于进一步分析 brief 中的 unresolved 间接调用。

输入：

```json
{
  "unresolved_id": "unresolved_call_004",
  "callsite_id": "callsite_01931",
  "max_candidates": 20,
  "include_registration_evidence": true
}
```

输出：

```json
{
  "status": "resolved_candidates",
  "candidates": [
    {
      "symbol_id": "decoder.c::decode_v1/1",
      "confidence": 0.64,
      "evidence": [
        "assigned in ops table at decoder.c:41"
      ]
    }
  ]
}
```

### 14.11 get_analysis_result

自动 brief 只包含压缩信息。Agent 需要时通过完整结果 ID 读取分页结果。

输入：

```json
{
  "full_result_id": "analysis_00017_v1",
  "section": "constraints",
  "offset": 0,
  "limit": 20
}
```

所有大型结果必须分页，禁止一次性返回无界内容。

---

## 15. 结果格式

系统必须同时维护两类结果：

1. 面向自动上下文注入的 `SinkAnalysisBrief`；
2. 面向显式工具下钻的完整跨函数分析结果。

完整结果至少包含：

```json
{
  "entrypoint": {
    "symbol_id": "fuzz.c::LLVMFuzzerTestOneInput/2",
    "location": {
      "file": "fuzz.c",
      "start_line": 12
    }
  },
  "target": {
    "symbol_id": "parser.c::copy_payload/1",
    "location": {
      "file": "parser.c",
      "start_line": 240
    }
  },
  "call_chain": [
    {
      "caller": "LLVMFuzzerTestOneInput",
      "callee": "parse_packet",
      "callsite_line": 17,
      "bindings": {
        "buf": "data + 4",
        "len": "size - 4"
      },
      "guards": [
        "size >= 8"
      ],
      "confidence": 0.95
    }
  ],
  "constraints": [
    {
      "expression": "size >= 8",
      "origin": "fuzz.c:15",
      "reason": "early_return_bypass"
    },
    {
      "expression": "size - 4 > HEADER_SIZE",
      "origin": "parser.c:101",
      "reason": "if_true_branch",
      "substituted": true
    }
  ],
  "sink_dataflow": {
    "sink": "memcpy",
    "arguments": {
      "source": {
        "expression": "packet->payload",
        "status": "partially_resolved"
      },
      "size": {
        "expression": "packet->length",
        "status": "partially_resolved"
      }
    }
  },
  "unresolved": [
    {
      "expression": "validate_checksum(packet)",
      "reason": "opaque_predicate"
    }
  ],
  "confidence": {
    "call_chain": 0.88,
    "constraints": 0.82,
    "dataflow": 0.71
  }
}
```

---

## 16. 缓存与增量分析

必须实现以下缓存层：

```text
L1  文件解析结果
L2  函数索引
L3  调用点索引
L4  函数摘要
L5  调用图
L6  路径查询结果
L7  Sink candidate enrichment 完整结果
L8  Sink Analysis Brief 与上下文 payload
```

缓存键至少包含：

```text
file content hash
grammar version
analysis version
configuration hash
```

文件未变化时：

- 不重新解析；
- 不重新生成函数摘要；
- 不重新计算局部 def-use；
- 只更新受影响调用边；
- 相同 sink candidate 复用自动分析结果；
- brief 内容未发生实质变化时不重复注入上下文。

---

## 17. 资源限制

提供统一配置：

```yaml
analysis_limits:
  max_files: 50000
  max_file_size_mb: 5
  max_function_lines: 5000
  max_call_depth: 8
  max_paths: 20
  max_candidates_per_call: 10
  max_constraints_per_path: 100
  analysis_timeout_seconds: 30

automatic_sink_enrichment:
  top_paths: 3
  max_call_depth: 6
  max_constraints_per_path: 12
  max_dataflow_steps_per_argument: 8
  context_token_budget: 1500
  timeout_seconds: 10
```

超过限制时返回：

```json
{
  "status": "partial",
  "reason": "path_limit_reached",
  "analyzed_paths": 20,
  "remaining_candidates": 73
}
```

禁止因为达到资源限制而返回空结果。

---

## 18. 目录结构建议

```text
analysis/
├── parser/
│   ├── c_adapter.py
│   ├── cpp_adapter.py
│   ├── queries/
│   └── node_utils.py
├── ir/
│   ├── expressions.py
│   ├── symbols.py
│   ├── calls.py
│   ├── constraints.py
│   └── serialization.py
├── index/
│   ├── repository_index.py
│   ├── symbol_index.py
│   ├── callsite_index.py
│   └── cache.py
├── cfg/
│   ├── builder.py
│   ├── blocks.py
│   ├── path_conditions.py
│   └── early_exit.py
├── summaries/
│   ├── function_summary.py
│   ├── return_summary.py
│   └── summary_builder.py
├── interprocedural/
│   ├── call_resolver.py
│   ├── call_graph.py
│   ├── argument_binding.py
│   ├── expression_substitution.py
│   ├── path_composer.py
│   └── path_ranking.py
├── dataflow/
│   ├── definitions.py
│   ├── local_slice.py
│   ├── provenance.py
│   └── field_tracking.py
├── runtime/
│   ├── sink_candidate.py
│   ├── enrichment_service.py
│   ├── brief_builder.py
│   ├── context_renderer.py
│   ├── trigger_policy.py
│   ├── deduplication.py
│   └── analysis_state.py
├── tools/
│   ├── analyze_sink_candidate.py
│   ├── index_repository.py
│   ├── find_callers.py
│   ├── find_paths.py
│   ├── summarize_function.py
│   ├── extract_constraints.py
│   ├── trace_value.py
│   ├── get_path_details.py
│   ├── resolve_callsite_candidates.py
│   ├── get_analysis_result.py
│   └── explain_path.py
└── tests/
    ├── fixtures/
    ├── unit/
    ├── integration/
    └── golden/
```

---

## 19. 实施顺序

### Phase 1：统一 IR 与仓库索引

实现：

- ExprIR；
- FunctionSymbol；
- CallSite；
- ConstraintIR；
- 文件扫描；
- 函数索引；
- 调用点索引；
- JSON 序列化；
- 文件级缓存。

验收：

- 能索引完整 C/C++ 仓库；
- 能列出函数和调用点；
- 所有实体有源码位置；
- 同名 static 函数不冲突。

### Phase 2：单函数调用点约束

实现：

- `if/else`；
- early-return 反转；
- `switch/case`；
- 短路逻辑；
- 循环上下文；
- 函数摘要；
- `extract_constraints`。

验收：

- 给定调用点，返回正确局部 guard；
- 所有 guard 有来源；
- opaque predicate 不丢失。

### Phase 3：候选调用图

实现：

- 直接调用解析；
- 跨文件同名候选；
- 成员调用候选；
- 简单函数指针；
- 回调注册模式；
- 调用图；
- Top-K 路径查询。

验收：

- 可从 target 反向找到多层 caller；
- 不唯一调用返回候选集合；
- 所有边有 confidence。

### Phase 4：参数绑定与跨函数约束组合

实现：

- actual/formal binding；
- ExprIR 结构化替换；
- 跨函数 guard 组合；
- 多路径保留；
- 约束归一化；
- `explain_path`。

验收：

- 至少支持三层直接调用；
- 参数替换准确；
- early-return 条件跨函数组合正确；
- 路径输出可直接供 Agent 阅读。

### Phase 5：局部反向数据流

实现：

- 局部 def-use；
- 字段访问；
- 条件赋值；
- phi；
- return summary；
- sink 参数 backward slice；
- `trace_value`。

验收：

- 能将 sink 参数追踪到函数参数或字段；
- 保留算术变换；
- 无法解析时明确中断点。

### Phase 6：自动 Sink Enrichment 与 Harness 集成

实现：

- SinkCandidate 数据结构；
- 自动触发策略；
- analyze_sink_candidate 服务入口；
- 自动分析预算；
- SinkAnalysisBrief；
- confirmed/candidate/inferred/unresolved 分类；
- XML 或等价结构化 context renderer；
- candidate 指纹与去重；
- brief 版本管理；
- suggested_queries；
- harness 分析状态机。

验收：

- Agent 提交 sink candidate 后无需额外工具调用即可获得基础路径摘要；
- 自动摘要遵守 context token budget；
- 自动摘要默认只返回 Top-3 路径；
- 相同 candidate 不重复运行或重复注入；
- brief 中的 path、unresolved 和 full result ID 可以用于继续下钻；
- 自动结果不会被标记为 Agent 自己的推理结论。

### Phase 7：性能与增量更新

实现：

- 多级缓存；
- 文件变更检测；
- 受影响函数重分析；
- 查询结果缓存；
- sink enrichment 缓存；
- brief 内容差异检测；
- 资源限制；
- partial result。

验收：

- 缓存命中后无需全仓库重扫；
- 相同 sink candidate 优先复用缓存；
- brief 未变化时不重复注入；
- 单次 Agent 查询不输出无界结果；
- 超时和超限返回部分分析结果。

---

## 20. Golden Test Cases

至少构建以下测试样本：

1. 同文件直接调用；
2. 跨文件三层调用；
3. 多个同名 static 函数；
4. early-return 后调用 sink；
5. nested `if`；
6. `switch` fallthrough；
7. 参数经过算术变换；
8. 返回值传递到下一层函数；
9. 简单函数指针；
10. 函数指针数组；
11. 结构体操作表；
12. 回调注册；
13. 多候选成员调用；
14. 递归调用；
15. 调用链中存在 opaque predicate；
16. sink 参数存在 phi；
17. 循环内调用 sink；
18. 调用图路径数超过限制；
19. 缓存增量更新；
20. 语法不完整文件的容错解析；
21. Agent 提交 sink candidate 后自动触发；
22. 相同 sink candidate 自动去重；
23. 自动 brief 超出 token budget 时正确裁剪；
24. Agent 根据 brief 中 path_id 下钻完整路径；
25. unresolved callsite 经显式工具进一步解析；
26. candidate 更新后生成 brief 新版本；
27. 自动分析未找到路径时返回可解释空结果。

每个样本必须提供人工标注：

```yaml
expected_functions: []
expected_callsites: []
expected_call_edges: []
expected_paths: []
expected_bindings: {}
expected_constraints: []
expected_dataflow: []
expected_unresolved: []
```

---

## 21. 核心验收指标

### 21.1 调用图

- 直接调用边 Precision ≥ 90%；
- 直接调用边 Recall ≥ 85%；
- 指定 target 的真实调用路径 Recall ≥ 80%；
- 所有非唯一调用均返回候选集合。

### 21.2 参数绑定

- 普通参数绑定准确率 ≥ 95%；
- 带算术表达式参数绑定准确率 ≥ 90%；
- 禁止使用字符串替换。

### 21.3 路径约束

- early-return 方向准确率 ≥ 95%；
- `if/switch` 关键条件召回率 ≥ 85%；
- 所有约束有源码位置；
- opaque predicate 保留率 100%。

### 21.4 数据流

- 局部 def-use 准确率 ≥ 85%；
- sink 参数追踪到函数参数或字段的成功率 ≥ 80%；
- 无法解析时不得输出虚假 attacker-controlled 结论。

### 21.5 Agent 运行时集成

- 有效 sink candidate 自动触发率 100%；
- 相同 candidate 的重复注入率为 0；
- 自动 brief 默认不超过配置的 context token budget；
- brief 中 path ID、unresolved ID 和 full result ID 可用率 100%；
- 所有自动结论正确分类为 confirmed、candidate、inferred 或 unresolved；
- 自动分析超时或超限时返回 partial，不返回无解释空结果。

### 21.6 性能

- 文件未变化时使用缓存；
- Agent 单次查询默认返回 Top 10；
- 超过限制时返回 partial；
- 不得把完整仓库 AST 直接返回给 Agent。

---

## 22. 必须遵守的实现原则

### 22.1 证据优先

任何结论都必须附带：

- 文件；
- 行号；
- 函数；
- 源码片段；
- 解析依据；
- 置信度。

### 22.2 事实与候选分离

输出中必须区分：

```text
facts
candidates
inferences
unresolved
```

### 22.3 不确定时保留候选

对于复杂函数指针、成员调用、宏和模板：

```text
unknown is acceptable
false certainty is not
```

### 22.4 优先反向分析

默认从 target 或 sink 向上寻找 caller 和 entrypoint，避免从所有函数正向展开。

### 22.5 输出规模受控

默认只返回：

- Top 10 路径；
- 每条路径关键约束；
- 相关参数绑定；
- sink 参数局部数据流；
- unresolved 节点。

### 22.6 自动轻量、显式深入

自动模式只负责生成受控的路径地图，不得默认展开完整分析。完整源码、更多路径和深层数据流必须通过显式工具获取。

### 22.7 分析能力必须可组合

每个模块必须输出稳定 IR，使以下流程可组合：

```text
find_paths
-> extract_constraints
-> bind_arguments
-> compose_constraints
-> trace_value
-> explain_path
```

---

## 23. 最终交付要求

完成后必须提供：

1. 可运行实现；
2. 完整类型定义；
3. Tree-sitter C/C++ Query 文件；
4. Repository Index；
5. Function Summary；
6. Candidate Call Graph；
7. Interprocedural Path Composer；
8. Local Def-Use；
9. SinkCandidate 协议；
10. Automatic Sink Enrichment Service；
11. SinkAnalysisBrief 与 Context Renderer；
12. Harness 触发策略与分析状态机；
13. Agent Tool API；
14. JSON Schema；
15. Golden Tests；
16. Benchmark 脚本；
17. `README.md`；
18. `ARCHITECTURE.md`；
19. `LIMITATIONS.md`；
20. `TASK_COMPLETION.md`。

`TASK_COMPLETION.md` 必须包含：

- 实际完成能力；
- 未完成能力；
- 目录结构；
- 核心算法；
- 测试数量；
- 指标结果；
- 已知限制；
- 后续建议；
- 至少三个真实代码库的运行结果；
- 至少五条完整 entrypoint-to-target 输出示例；
- 至少五个 sink candidate 自动增强示例；
- 自动 brief 的 token 预算、裁剪和去重测试结果；
- 自动 brief 到显式工具下钻的完整运行轨迹。

最终系统应使 Agent 能够通过一个目标函数、调用点或 sink，快速获得：

```text
自动注入的 Sink Analysis Brief
+ 候选调用链
+ 参数绑定
+ 调用点约束
+ 跨函数约束
+ sink 参数来源
+ 源码证据
+ 未解析项
+ 可继续下钻的分析 ID 与工具建议
```

这就是本次 Tree-sitter-based 能力改造的完整目标。
