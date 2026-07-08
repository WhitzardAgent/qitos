# CyberGym PoC Agent 设计改造清单

> 本文档记录我们从接手以来对 CyberGym PoC 生成 Agent 所做的全部改造与优化，按功能模块组织，供团队成员参考。

---

## 一、核心设计理念转变

### 从"试错法"到"约束满足"

我们接手时 Agent 的核心问题：**靠随机提交 PoC 碰运气，而不是系统性地理解和绕过每一个验证条件**。

典型案例：arvo:17986（GraphicsMagick EXIF 堆溢出）——Agent 找到了漏洞点 `GenerateEXIFAttribute`，但无法系统性地绕过 EXIF magic、TIFF header、IFD 格式等验证检查，陷入了 `candidate_required` 死循环只能反复提交无法调查。而成功方案利用了 `oval+n` 整数溢出，这是 Agent 从未考虑的路径。

**核心教训**：安全分析的本质是**约束满足**——每一个 if/memcmp/switch 都是一个 gate，PoC 必须依次通过所有 gate 才能到达漏洞点。不是试错，而是逐个确认和绕过。

---

## 二、上下文管理：让 Agent 在长对话中不丢失关键信息

### 2.1 任务持久化记忆（Task-Persistent Memory）

**问题**：上下文压缩（compaction）会丢弃早期关键信息——漏洞描述、尝试历史、当前假设等在压缩后丢失，导致 Agent 重复犯同样的错误。

**方案**：在 State 中增加四个字段，这些字段**不会被上下文压缩丢弃**，每轮都会渲染到观测中：

| 字段 | 内容 | 更新时机 |
|------|------|---------|
| `vulnerability_analysis` | 漏洞类型 + sink 函数 + 触发假设 + 已确认 gate 条件 | 进入 formulation 阶段时 |
| `path_trace` | 入口到 sink 的函数调用链 | 每步更新 |
| `attempt_history_compact` | 结构化提交历史：版本号、结果、gate、crash 信息、action hint | 每次 submit 后 |
| `current_hypothesis` | 当前假设——根据失败 gate 分类生成具体的下一步策略 | 每次非 accepted 的 submit 后 |

**关键设计**：`attempt_history_compact` 不是简单的文本日志，而是结构化信息：

```
#1 poc_v1.bin: no_trigger [path_not_reached] → route input to vulnerable function
#2 poc_v2.bin: vul_crash(1) [vul_only_triggered] crash=heap-buffer-overflow @ attribute.c:1553 → refine for precision
#3 poc_v3.bin: no_trigger [carrier_parse] → fix magic bytes/headers
```

每条记录包含：结果分类、失败 gate、crash 详情、下一步动作提示。

### 2.2 假设更新机制（Hypothesis Map）

**问题**：旧版 Agent 的 `current_hypothesis` 只有两种模板（"Path not reached" 和 "触发成功"），无法给出具体的修复方向。

**方案**：建立完整的 9 类 gate → 假设映射：

| 失败 gate | 假设内容 |
|-----------|---------|
| `path_not_reached` | 路径未到达——列出第一个未确认的 gate，要求先确认 |
| `carrier_parse` | 输入格式被拒绝——修复 magic bytes/header 结构 |
| `malformed_substructure` | 子结构无效——修复字段偏移/大小 |
| `trigger_wrong_signature` | crash 类型不对——调整溢出大小/偏移 |
| `trigger_wrong_location` | crash 位置不对——调整目标字段 |
| `discriminant_failed` | 修复版也 crash——减少溢出到最小（1-4 字节） |
| `vul_only_triggered` | 仅漏洞版 crash——优化精度 |
| `timeout_not_crash` | 超时但没 crash——简化 PoC |
| `duplicate_candidate` | 重复提交——修改 PoC 内容 |

### 2.3 优先级驱动的上下文压缩（Priority-Based Compaction）

**问题**：旧版上下文压缩对所有消息一视同仁，关键信息（submit_poc 结果、parser 代码）和低价值信息（普通 READ）被同等对待。

**方案**：为 ToolResult 添加 `compaction_priority` 元数据：

| 优先级 | 保护阈值 | 适用对象 |
|--------|---------|---------|
| `critical` | 20K chars | submit_poc 结果 |
| `high` | 8K chars | parser/field/seed 路径的 READ 结果 |
| normal | 15K chars / 300 lines | 普通 READ |
| normal（无保护） | 40K chars / 800 lines | 其他工具输出 |

### 2.4 早期 READ 裁剪（EARLY_READ_SNIP）

**问题**：大量 READ 输出占据上下文窗口，挤掉了更重要的信息。

**方案**：3 轮之后，将普通优先级的旧 READ 消息替换为事实导向的摘要预览：

- 提取第一个函数签名 / struct / #define
- 提取最后一个有意义的行（非注释/空行）
- 显示行数统计
- 附带匹配的 `durable_code_facts`

高优先级和关键优先级的 READ 永远不被早期裁剪。

### 2.5 压缩后恢复（Post-Compact Restoration）

**问题**：即使有持久化字段，上下文压缩后 Agent 可能丢失最新的关键状态。

**方案**：压缩后注入恢复消息，包含：漏洞描述、ready PoC 内容、最后错误追踪、最佳 PoC 分数/路径、最近 submit 结果摘要、输入格式模型摘要。

---

## 三、约束系统：从抽象标签到可执行的 PoC 构造蓝图

### 3.1 调用链追踪（CallChain + ChainGate）

**问题**：Agent 没有结构化地追踪"输入如何到达漏洞点"的路径，无法系统性地识别和绕过路径上的验证条件。

**方案**：引入两个数据结构：

**ChainNode** —— 调用链上的函数节点：
```python
ChainNode(
    location="attribute.c:1553",
    function="GenerateEXIFAttribute",
    role="sink",           # entry / parser / dispatch / guard / sink
    description="EXIF IFD parser with heap buffer overflow",
    status="confirmed",     # confirmed / inferred / unknown
    evidence="READ attribute.c",
    order=2                 # 在链中的位置
)
```

**ChainGate** —— PoC 必须满足的条件：
```python
ChainGate(
    node_order=2,
    gate_type="bounds_gate",  # format / dispatch / path / bounds / value
    description="BYTE format case lacks pval bounds check",
    required_condition="IFD entry with format=BYTE (1) and n>4 where offset+size passes check",
    status="confirmed",       # confirmed / inferred / refuted / bypassed
    evidence="READ attribute.c:1887-1905",
    repair_hint="Use oval that wraps on 32-bit addition"
)
```

**Gate 的生命周期**：
- `inferred` → 通过 READ 源码确认 → `confirmed`
- `inferred` → 通过 READ 源码否定 → `refuted`（附带 repair_hint）
- 提交失败时自动 refute 匹配的 gate
- refuted gate 永远不删除——它们承载学习，防止重犯

### 3.2 Gate Refutation 机制

**问题**：Agent 反复尝试同样的失败策略，不知道某个方向已经验证不可行。

**方案**：每次 submit_poc 失败时：
1. 分类失败 gate（9 类）
2. 在 `call_chain_gates` 中查找描述匹配的 gate
3. 将其标记为 `refuted`，附带：
   - `evidence`：为什么失败
   - `repair_hint`：应该怎么改

**示例**：
```
Gate: "overflow by writing 1000 bytes past buffer" → refuted
Evidence: "vul_crash but fix also crashes — discriminant failed"
Repair hint: "reduce overflow to minimal (1-4 bytes past boundary)"
```

### 3.3 约束板重设计：PoC 构造蓝图

**问题**：旧版约束板使用自创的分类标签（`[format_gate]`、`[bounds_gate]`），LLM 训练数据中从未见过这种形式化，无法理解。而且约束板"描述"漏洞但"不指导"如何构造 PoC。

**核心洞察**：大模型是在人类语言上训练的，应该让约束板读起来像一个资深安全研究员的笔记——一个初级分析人员拿起来就能构造 PoC 的那种。

**方案**：将约束板重构为五个自然语言段落：

```
## Vulnerability
EXIF IFD parser with heap buffer overflow - BYTE/SBYTE cases lack pval bounds
validation unlike other format cases
Call path: LLVMFuzzerTestOneInput → GetImageAttribute → GenerateEXIFAttribute

## PoC Requirements
- Input must satisfy: Profile data starts with 45 78 69 66 00 00
- Input must satisfy: Bytes after Exif\0\0 must be 49 49 2A 00 or 4D 4D 00 2A
- Set field values so that: IFD entry with format=BYTE (1) and n>4 where
  offset+size passes the (oval+n)>length check but pval+n extends beyond buffer

## PoC Byte Layout
```
Offset   Bytes              Purpose
------   -----              -------
0x0000   45 78 69 66 00 00  EXIF profile must start with Exif\0\0 magic bytes
0x0006   49 49 2A 00        TIFF header must have valid byte order and magic 0x002a
------   Fixed bytes above; variable fields below ------
         BYTE format case lacks pval bounds check
           Condition: IFD entry with format=BYTE (1) and n>4 where offset+size
           passes the (oval+n)>length check but pval+n extends beyond buffer
```

## Failed Approaches
- oval=0xFFFFFFFF, n=5: no trigger → use value that wraps past buffer start

## Unresolved Questions
- pval offset computation: is tiffp_max correctly computed?
  Need to confirm: pval = tiffp + oval wraps to address within heap region
```

**关键创新——PoC Byte Layout**：
- 从 LLM 自己写的 `required_condition` 中提取 hex 字节序列
- 支持两种格式：`45 78 69 66 00 00`（空格分隔）和 `0x49 0x49 0x2A 0x00`（0x 前缀）
- 布局为 offset 表：`0x0006   49 49 2A 00   TIFF byte order + magic`
- 变量字段（bounds_gate 等）列在固定字节下方
- 优雅降级：当无法提取 hex 字节时（如 ieee1722 离线解析器），退化为字段约束列表

**Gate 类型到自然语言的映射**：
| 内部类型 | LLM 看到的 |
|---------|-----------|
| `format_gate` | "Input must satisfy: ..." |
| `bounds_gate` | "Set field values so that: ..." |
| `dispatch_gate` | "Route input through: ..." |
| `path_gate` | "Satisfy branch condition: ..." |
| `value_gate` | "Set specific value: ..." |

LLM **永远看不到** `format_gate`、`bounds_gate` 这些内部标签。

### 3.4 构造前推导清单（Pre-Construction Derivation Checklist）

**问题**：Agent 在 gate 条件还不具体的情况下就开始写 PoC 代码，浪费推理 token 在模糊的描述上。

**方案**：当存在已确认 gate 时，在 formulation 阶段强制 Agent 在写 PoC 代码**之前**先推导：

1. 固定字节要求：在什么偏移量必须出现什么字节？
2. 字段约束：什么值触发漏洞？计算精确数值
3. 计算：PoC 总大小 = header bytes + field bytes + overflow data
4. 验证：PoC 是否满足 "PoC Requirements" 中列出的每一个要求？

Agent 必须将这些推导写成 Python 注释，然后才写 PoC 代码。

### 3.5 Gate-Repair 纪律

**问题**：Agent 在第一个 gate 还是 "inferred" 状态时就构造 PoC，导致反复失败。

**方案**：在 verification 阶段，如果第一个未确认 gate 的状态仍是 "inferred"：
- **强制 Agent READ 相关源码**确认或否定该 gate
- **不允许构造新 PoC** 直到该 gate 被确认或否定
- 显示 refuted gate 作为学习信号

---

## 四、反馈闭环：让每次失败都产生学习

### 4.1 失败 gate 分类（Failed Gate Classification）

**问题**：旧版只区分"触发"和"未触发"，没有细粒度的失败原因。

**方案**：9 类失败 gate 分类，每类对应具体的修复策略：

```python
def _classify_failed_gate(result) -> str:
    # 1. path_not_reached: vul_exit=0, no crash → 输入没到达漏洞路径
    # 2. carrier_parse: vul 信号为 SIGABRT/SIGSEGV 但位置在 harness → 格式被拒绝
    # 3. malformed_substructure: crash 但不在目标函数 → 子结构无效
    # 4. trigger_wrong_signature: crash 在目标函数但 crash type 不对
    # 5. trigger_wrong_location: crash 但位置偏了
    # 6. discriminant_failed: fix 版也 crash → PoC 太激进
    # 7. vul_only_triggered: 仅漏洞版 crash，精度未验证
    # 8. timeout_not_crash: 超时但没 crash
    # 9. duplicate_candidate: 重复 PoC
```

### 4.2 结构化事实提取（Deterministic Fact Extraction）

**问题**：READ 的代码内容在上下文压缩后丢失，Agent 反复读取同一文件。

**方案**：从 READ 内容中确定性提取关键事实到 `durable_code_facts`：

- `const: MaxTextExtent = 8192 (in attribute.c)` —— #define 常量
- `buffer_size: 8192 (in attribute.c)` —— 缓冲区大小
- `field_offset: pde+8 = 8 (in attribute.c)` —— 结构体字段偏移
- `var_type: oval = unsigned long (in attribute.c)` —— 关键变量类型
- `func: GenerateEXIFAttribute (in attribute.c)` —— 函数签名

这些事实不受上下文压缩影响，Agent 不需要重新 READ 就能引用。

### 4.3 Harness 入口自动确认

**问题**：Agent 不确定输入如何到达解析器，浪费时间在无关路径上。

**方案**：当 READ 或 FindSymbols 结果中包含 `LLVMFuzzerTestOneInput` 或 `int main(` 时：
- 自动设置 `harness_entry_confirmed = True`
- 自动填充 `InputFormatModel` 的 entry_point 和 input_path
- 添加 `[confirmed] harness_entry: LLVMFuzzerTestOneInput in fuzzing/coder_fuzzer.cc` 到 code_facts

### 4.4 连续失败干预

**问题**：Agent 连续多次提交 NO_TRIGGER 的 PoC，不反思为什么到达不了漏洞。

**方案**：
- 4 次连续 NO_TRIGGER 后，强制插入提醒："STOP submitting — READ the harness entry and trace the call chain"
- 3 次连续提交错误后，清空 ready_pocs 队列，强制回到 investigation
- 提交预算耗尽时，提示 Agent "READ 源码理解为什么输入到达不了 sink"

---

## 五、工具增强：让 Agent 更高效地获取信息

### 5.1 格式感知工具箱（Format-Aware Toolbox）

**问题**：Agent 对二进制格式（PNG/JPEG/BMP/WAV/ZIP/PDF）的构造一无所知，每次都从零手写。

**方案**：提供 carrier generator 工具，一键生成合法格式的容器文件：

- `generate_carrier(format, payload)` — 将 payload 嵌入合法的格式容器中
- `mutate_binary(path, offset, bytes)` — 在指定偏移处修改二进制文件
- `inspect_binary(path)` — 显示文件结构和字段值

Agent 不需要手写 PNG header 或 JPEG marker，直接构造 payload 然后用 carrier 包装。

### 5.2 FindSymbols 签名增强

**问题**：旧版 FindSymbols 只返回函数名，Agent 需要再 READ 才能看到签名。

**方案**：FindSymbols 结果现在包含函数签名，Agent 通常不需要再 READ 就能理解 API：

```
FUNC  attribute.c:1553  | static MagickBooleanType GenerateEXIFAttribute(const Image *image, const char *)
FUNC  attribute.c:2414  | static MagickBooleanType GetImageAttribute(const Image *image, const char *)
```

### 5.3 输入格式结构化模型（InputFormatModel）

**问题**：Agent 对输入格式的理解散落在各处（漏洞描述、corpus 文件、harness 代码），没有统一模型。

**方案**：引入 `InputFormatModel` dataclass：

```python
InputFormatModel(
    format_type="jpeg_with_exif",    # 输入格式类型
    entry_point="LLVMFuzzerTestOneInput",  # harness 入口
    input_path="buffer",             # 输入如何传递
    magic_bytes="FF D8 FF E1",       # magic bytes
    sample_paths=["seeds/1.jpg"],    # 种子文件路径
    mutation_strategy="corpus_mutate",  # 推荐的变异策略
    container_structure="JPEG APP1 + EXIF TIFF",  # 容器结构
    confirmed=True                    # 是否已确认
)
```

### 5.4 文件读取追踪（Read Coverage）

**问题**：Agent 反复 READ 同一文件的相同行范围，浪费步骤。

**方案**：`read_coverage` 字典追踪每个文件已读取的行范围：

```python
{"attribute.c": [(1, 80), (1500, 1600), (1880, 1920)]}
```

在 Working Memory 中显示，防止重复读取。

### 5.5 PoC 归档

**问题**：Agent 重新提交相同路径的 PoC 时覆盖旧文件，无法回溯。

**方案**：每次 submit_poc 时将 PoC 复制到 `.cybergym/poc_archive/poc_v1.bin`、`poc_v2.bin` 等，保留所有历史版本。

---

## 六、Prompt 工程：让 LLM 按正确的方式思考

### 6.1 阶段操作指引（Phase Operating Guidance）

每个阶段有针对性的 prompt 段落：

**Investigation 阶段**：
- 如果有 open gates，提醒确认第一个
- 提供 `record_chain_node` 和 `record_gate` 的具体调用示例
- 列出所有 gate 类型及其含义

**Formulation 阶段**：
- 无约束时强制提取至少一个触发条件
- 有确认 gates 时显示 Pre-Construction Derivation Checklist
- 不允许在第一个 open gate 还是 "inferred" 时构造 PoC

**Verification 阶段**：
- 显示 discriminant failure / partial-hit 指引
- 显示失败 gate + repair hint
- Gate-repair 纪律：强制 READ 确认未确认的 gate
- 显示 refuted gates 作为学习信号

### 6.2 候选构造工具过滤

**问题**：Agent 在应该构造 PoC 的时候还在做无关的 GREP/READ。

**方案**：根据状态动态过滤允许的工具列表：
- `pending_reflection` → 只允许 `record_reflection`
- `pending_chain_checkpoint` → 只允许 `record_chain_node`/`record_gate` + 定向 READ/GREP
- `candidate_ready` → 只允许 `submit_poc` + `record_reflection`
- `candidate_required` → 限制为构造相关的工具

### 6.3 格式感知的 Bug 类型指导

**问题**：不同漏洞类型需要不同的 PoC 构造策略，旧版一视同仁。

**方案**：根据 `bug_type` 和 `poc_strategy` 加载针对性的指导文档：

| bug_type | 额外指导 |
|----------|---------|
| `buffer_overflow` + `binary_python` | 二进制构造的溢出技巧 |
| `buffer_overflow` + `corpus_mutate` | 基于 seed 变异的溢出技巧 |
| `buffer_overflow` + `hex` | hex 编辑的内存布局技巧 |
| `use_after_free` + `binary_python` | UAF 的二进制触发技巧 |
| `integer_overflow` + `hex` | 整数溢出的 hex 布局技巧 |

---

## 七、架构重构：让代码可维护

### 7.1 Mixin 拆分

**问题**：旧版 `agent.py` 超过 3000 行，所有逻辑混在一起。

**方案**：拆分为 6 个 Mixin：

| Mixin | 职责 |
|-------|------|
| `StateInitMixin` | 状态初始化 |
| `TaskAnalysisMixin` | 任务描述分析 |
| `RepoAnalysisMixin` | 仓库结构分析 |
| `CrashParsingMixin` | ASAN/crash 输出解析 |
| `PromptsMixin` | 系统提示词构建 |
| `HarnessMixin` | Harness 检测 |
| `PathMixin` | 路径工具 |
| `ValidationMixin` | 候选验证和 gate 分类 |
| `CandidateFamilyMixin` | 候选族管理 |
| `FeedbackMixin` | 反馈处理 |
| `ObservationMixin` | 观测构建和状态渲染 |
| `ToolMixin` | 工具注册和执行 |

### 7.2 工具名称常量化

**问题**：工具名称散落在代码各处，修改一个工具名需要改十几个地方。

**方案**：所有工具名常量集中在 `tool_names.py`，通过 import 引用。

### 7.3 TUI 日志全量显示

**问题**：开发阶段 TUI 日志中的截断阻碍行为分析。

**方案**：移除所有 `_clip()` 调用、`[:N]` 切片和 "truncated" 消息：
- `observations.py`：移除所有 `_clip()` 和字符串切片
- `agent.py`：移除 hypothesis `[:400]`、analysis `[:600]`、stderr `[:2000]` 等限制
- `tool_render.py`：移除 GREP/GLOB/FindSymbols 的结果数量限制和截断提示，BASH stdout/stderr 不再截断行数
- `feedback.py`：移除 hot feedback 和 error trace 的截断

---

## 八、设计原则总结

1. **约束驱动**：安全分析 = 约束满足。每一步都应该是确认或绕过一个 gate，而不是随机尝试。

2. **自然语言优先**：对 LLM 的输入应该用自然语言，不要用自创的形式化标签。LLM 是在人类语言上训练的。

3. **失败即学习**：每次失败都应该产生结构化学习——refuted gate 带 repair_hint，attempt_history 带 gate 分类和 action hint。

4. **关键信息不可丢弃**：上下文压缩不应该丢失关键信息。持久化字段 + 优先级保护 + 压缩后恢复三重保障。

5. **优雅降级**：所有优化必须对所有漏洞类型有效，不能只对当前一个 case 优化。PoC Byte Layout 在有 hex 数据时显示字节表，没有时退化为字段约束列表。

6. **强制推导在行动前**：Agent 必须先推导出具体的字节值和偏移量，然后才写 PoC 代码。模糊的描述不是足够的输入。

7. **开发阶段不截断**：调试 Agent 行为时需要看到完整内容，截断是生产优化，不是开发工具。
