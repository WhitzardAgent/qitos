# 02 — 收紧 TaskSpec、symbol、file、format 与 strategy 初始化

优先级：P0/P1  
依赖：[00](00-boundaries-and-baseline.md)；可与 01 并行开发但独立提交  
禁止：LLM bootstrap call、新 parser 依赖、本地执行

## 最新再审计状态

状态：**未开始**。

最新改动扩展了 constraint extraction 的 `memcmp` 类函数，但没有修改 `task_spec.py` 或 `HarnessMixin` 的宽松 description heuristics。因此：

- `behavior -> avi`、`type/improper -> pe`、`target/start -> tar` 风险仍在；
- `_SYMBOL_RE` 仍会把普通英文词填入 symbols；
- `poc_strategy` 仍按 bug label/裸子串产生 `binary_python/hex/corpus_mutate/text`；
- 新 `poc_iteration.md` 又把 corpus-first 写成 `ALWAYS`，进一步放大错误 bootstrap。

本任务除原验收外，必须给任务 07 提供可直接展示的 `unknown/direct_construct/qualified_seed_edit/structured_construct` 值；不要在 prompt 内再次猜 strategy。

## 目标

让 bootstrap 提供少量高精度 search anchor，并在证据不足时保留 unknown。修复全量 1507 任务审计发现的普通英文伪 symbol 和 `avi/pe/tar` 裸子串误路由。

## 必须复用

- `task_spec.py::extract_task_spec_deterministic()`
- `agent_impl/task_analysis.py`
- `agent_impl/harness.py::_build_input_format_model()`
- `agent_impl/harness.py::_detect_poc_strategy()`
- `agent_impl/state_init.py::init_state()`
- 现有 state 字段：`symbols_mentioned`、`source_files_mentioned`、`input_format`、`poc_strategy`

不要创建 `task_spec_v2.py`，不要保留新旧两套 classifier。

## 文件

- 修改 `task_spec.py`
- 修改 `agent_impl/task_analysis.py`
- 修改 `agent_impl/harness.py`
- 修改 `agent_impl/state_init.py`
- 测试新增 `tests/test_task_spec_precision.py`
- 可新增离线只读审计脚本 `scripts/audit_task_spec.py`

## 数据规则

### 1. Anchor 必须带来源与置信度

在 `build_task_spec()` 返回值中新增紧凑 `anchors` 列表；legacy flat fields 从它派生：

```python
{
    "kind": "symbol" | "source_file" | "format" | "bug_class",
    "value": "pj_default_destructor",
    "source": "description" | "error_txt" | "patch_diff" | "harness_info",
    "evidence": "原文短 span",
    "confidence": 0.0,
}
```

不要把 anchors 逐个增加成 state 顶层字段。可以把完整列表放在 `state.metadata["task_spec_anchors"]`，现有 flat fields只保存高置信结果。

### 2. symbol 提取只接受语法证据

允许：

- backtick/引号中的 identifier；
- `function X`、`method X`、`in X()`；
- `ns::Class::method`；
- 含下划线且不像普通句子词的 identifier；
- 明显 CamelCase 且长度合理；
- patch diff 中函数定义/调用上下文。

禁止：

- 单纯 `_SYMBOL_RE` 捕获所有英文单词；
- `read`、`function`、`buffer`、`occurs` 等普通词；
- 仅因首字母大写就认为是 symbol。

输出按 confidence、原文顺序排序，最多 12 个。`likely_entrypoints` 只能从合格 symbol 派生。

### 3. source file 支持 bare filename

接受 `parser.c`、`valid.c`、`foo.hpp`，不再要求包含 `/`。但图片/输入文件扩展名与源码文件必须分 kind，避免 `.png` 进入 `source_files_mentioned`。

### 4. format 判断使用 token boundary 和证据优先级

优先级：

```text
harness source / fuzzer target / explicit build target
> qualified in-repo seed signature
> description 中独立格式 token 或扩展名
> unknown
```

所有短 token 使用边界：`AVI`、`PE`、`TAR`。必须覆盖回归：

- `behavior` ≠ AVI；
- `type` / `improper` / `operation` ≠ PE；
- `target` / `start` ≠ TAR；
- `document` ≠ DOC；
- `profile` ≠ ELF/file format。

### 5. strategy 不再由 bug label 决定

`heap-buffer-overflow` 本身不能推出 binary。`poc_strategy` 初始值仅允许：

- `direct_construct`：描述/entry 明确是文本、argv 或小型确定性 payload；
- `qualified_seed_edit`：存在静态证据支持的同族 seed；
- `structured_construct`：harness/format 有明确结构证据；
- `unknown`：其他情况。

保留旧值的兼容映射只能用于读历史 state，不得在新 bootstrap 继续生成 `binary_python/hex/corpus_mutate/text` 作为求解策略。编码方式以后由 candidate 选择。

### 6. 删除自动 chain truth

description anchors 只能生成 search candidates；`state_init._precompute_call_chain()` 不得把它们直接写成 confirmed node/gate。若保留预计算，只能标 `hypothesized` 并带 repo-index evidence。

## 测试矩阵

至少 30 个 table-driven fixtures：

- 10 个短子串 false positive；
- 5 个 bare source filename；
- 5 个真实 qualified symbol；
- 5 个完全模糊描述，期望 anchors 为空/unknown；
- 5 个 harness evidence 覆盖 description 弱线索。

关键断言：

```python
assert infer("undefined behavior") != "video"
assert infer("improper type operation") != "pe"
assert infer("target starts parsing") != "tar"
assert "valid.c" in source_files
assert "occurs" not in symbols
```

离线审计脚本只读 `tasks.json`，输出 JSON/Markdown 汇总，不写 state，不访问网络。至少报告：unknown 比例、每任务 symbol 数、format 分布、已知短子串误判数。

## 验收

- 已知 `avi/pe/tar` false positive 为 0；
- 模糊描述允许没有 symbol/format/strategy；
- `symbols_mentioned` 不再 1507/1507 非空；
- bare `.c/.cpp/.h` 文件名被识别；
- harness 强证据可覆盖 description；
- prompt 不展示低置信 anchor 为事实；
- 没有新增 LLM 调用或依赖。

## 不要做

- 不扩展固定格式 toolbox。
- 不从项目名猜格式并标 confirmed。
- 不把 `unknown` 当错误或阻塞提交。
- 不修改 feedback、phase 或 candidate schema；属于后续任务。
