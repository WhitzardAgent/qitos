# 03 — 建立静态 ScopeMap 与 HarnessContract

优先级：P1  
依赖：[02](02-bootstrap-precision.md)  
禁止：本地构建/运行、fuzzing、debugger、instrumentation

## 最新再审计状态

状态：**未开始**。

两模式新增了“先找 harness entry”的 prompt，但数据层仍没有 `ScopeMap` 或 `HarnessContract`。当前 work order 只是固定提示 Grep `LLVMFuzzerTestOneInput/main`，不能区分：

- source root 外的 maintainer harness；
- 多个 fuzzer target；
- CLI/stdin/buffer/sequence input mode；
- target、dependency、test、generated、build 角色。

不要再增加一段 harness prompt 作为替代；按本任务复用 repo index 并形成 typed static artifact。

## 目标

把“提交文件如何进入目标代码”变成每个任务最先形成的静态 artifact。只从源码、build scripts、adapter/harness metadata 和 task workspace 文件推导；官方 `submit_poc` 仍是唯一动态 oracle。

## 必须复用

- `adapter.py` 已提供的 `source_root`、`repo_dir`、`repo_archive_root`、`harness_info`
- `agent_impl/repo_index.py::build_repo_index()`
- `repo_index.py::_extract_harness_entries()`
- `lookup_symbol()`、`reverse_call_lookup()`、`find_dispatch_tables()`、`find_indirect_dispatch()`
- `HarnessMixin._discover_fuzzer_target()`
- `InputFormatModel` 作为 legacy compatibility view

不要创建第二个 repo crawler、第二个 call graph 或新工具集。

## 文件

- 新增 `agent_impl/contracts.py`（只放小型 dataclass 和 validator）
- 修改 `state.py`
- 修改 `agent_impl/state_init.py`
- 修改 `agent_impl/harness.py`
- 修改 `agent_impl/repo_index.py`
- 修改 `agent_impl/observations.py`
- 测试新增 `tests/test_static_task_contracts.py`

## 数据模型

### ScopeEntry

```python
@dataclass
class ScopeEntry:
    path: str
    role: str          # target | harness | dependency | test | generated | build
    inclusion: str     # primary | contextual | excluded
    reason: str
    evidence_ref: str
    confidence: float
```

### HarnessContract

```python
@dataclass
class HarnessContract:
    entry_symbol: str = ""
    entry_path: str = ""
    input_mode: str = "unknown"  # file | stdin | argv | buffer | sequence | unknown
    input_slice: str = "unknown"
    format_type: str = "unknown"
    build_target: str = ""
    invocation_hint: str = ""
    seed_paths: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
```

只在 `CyberGymState` 增加两个嵌套字段：`scope_map` 和 `harness_contract`。不要把每个 contract 字段再复制成顶层 state。现有 `input_format` 暂时由 contract 派生供旧 prompt/tool 使用，禁止反向覆盖 contract。

## 静态提取顺序

### 1. 合并两个可见 root

- `source_root`：主要待审计源码；
- `repo_archive_root`：用于查找外层 build/fuzz/test/harness；
- 相同 realpath 去重；
- 所有路径最终标准化为 workspace-relative 或注明 root id。

不要因为文件在 source root 外就自动当 dependency；按角色判定。

### 2. 扩展现有 repo index，不另扫一遍

`build_repo_index()` 在原返回 dict 增加：

```text
build_files
harness_candidates
generated_files
test_fixture_paths
```

仍复用 `_gather_source_files()` 和 max file 限制。对 build files 只读取有限大小并匹配现有 target 名、source path、fuzzer marker。

### 3. harness candidate 排序

证据强度：

1. adapter/task metadata 明确 harness；
2. `LLVMFuzzerTestOneInput` 或明确 fuzz entry 定义；
3. build script 把 entry 编入 target；
4. CLI main 中明确读取 argv/stdin/file；
5. description 推测。

不得返回“遇到的第一个 fuzzer name”。收集候选、打分、保留 top 5，并解释每个 score；只有唯一高置信候选才填 contract entry。

### 4. input mode 静态判定

- buffer：entry signature/代码直接接收 bytes+size；
- file/argv：main/harness 打开 argv path；
- stdin：显式 `stdin/read(0)/fread(stdin)`；
- sequence：harness 对同一输入进行多步 operation；
- 证据冲突或缺失：unknown。

不要根据字符串中出现 `file` 就认定 file mode。

### 5. seed 只做静态资格检查

`seed_paths` 只包含满足以下至少两项的 workspace 内文件：

- 与 format extension/magic 相符；
- 位于对应 harness/test/fixture/corpus 路径；
- 被 build/test/harness source 引用；
- 文件大小在提交限制内；
- provenance 明确。

不运行 seed，不把“存在文件”当作 harness 接受证明。记录 qualification reasons。

### 6. observation 只展示紧凑缺口

示例：

```text
HARNESS CONTRACT
- entry: LLVMFuzzerTestOneInput @ fuzz/foo_fuzz.cc:31 [source evidence]
- input: buffer, whole submitted bytes
- format: unknown
- qualified seeds: tests/data/minimal.foo [2 static reasons]
- unresolved: dispatcher from entry to described target
```

每步不重复完整 scope map；只有 contract 变化或 mode 需要时展示。

## 测试 fixture

用临时小仓库覆盖：

1. fuzz entry 在 source root 外层；
2. 两个 fuzzer target，只一个引用 target module；
3. CLI argv file input；
4. stdin input；
5. callback/table dispatch；
6. generated lexer/parser；
7. vendor 内同名 parser，不应成为 primary；
8. 没有任何 harness，contract 保持 unknown；
9. seed 仅扩展名相符但无其他证据，不 qualified；
10. source root == archive root 时不重复索引。

## 验收

- 每个非 unknown contract 字段至少有一个 evidence ref；
- root 外 maintainer harness 可被发现；
- dependency/test/generated/build 角色可区分；
- 未知 input mode 不默认 file/stdin/buffer；
- seed qualification 不执行目标、不访问网络；
- repo 只被一个共享 index pipeline 扫描；
- legacy `input_format` 与 contract 单向同步且测试覆盖。

## 不要做

- 不解析完整 build system。
- 不引入 Docker、compile command 或 binary introspection。
- 不承诺静态 contract 等于 runtime truth；保留 confidence/unknown。
- 不在本任务修改 candidate/feedback/stop。
