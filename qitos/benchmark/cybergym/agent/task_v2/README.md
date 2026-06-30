# CyberGym Agent V2 可落地优化任务清单

日期：2026-06-29  
依据：[issues/010](../issues/010-cybergym-agent-architecture-audit-2026-06-29.md)、[issues/011](../issues/011-cybergym-task-solving-effectiveness-2026-06-29.md)、[issues/012](../issues/012-anthropic-microsoft-lessons-for-cybergym-agent-2026-06-29.md)  
目标：提高 Level 1 “漏洞描述 → 静态探索代码库 → 构造 raw-input PoC → 官方 `submit_poc` crash”成功率。

最新再审计：[CURRENT_STATE.md](CURRENT_STATE.md)（HEAD `6a00a33` + 当前未提交两模式改动）

## 2026-06-29 最新状态

当前工作树已经部分实现两模式，但**尚未完成任何一个 V2 task**。不要把“已有同名字段/文件”误判为任务完成：

| 任务 | 当前状态 | 最新判断 |
|---|---|---|
| 00 | 部分完成 | 已有分析文档；24-task manifest 和基线记录仍缺失；当前为 10 pass / 3 fail |
| 01 | 未开始 | action id 未进 runtime context/ToolResult；global last-result 仍存在；submit 仍并发安全 |
| 02 | 未开始 | 原有宽松 symbol/format/strategy heuristic 未修复 |
| 03 | 未开始 | 没有 typed `ScopeMap/HarnessContract`；仍只依赖 legacy `InputFormatModel` |
| 04 | 反方向部分实现 | 新增 chain score/order/work-order，但仍是自报 confirmed，render 仍会 mutation/auto-promote |
| 05 | 未开始 | candidate identity/provenance 仍未统一为 bytes SHA-256 contract |
| 06 | 回归加重 | no-crash/path_not_reached 现在会 refute frontier、降级更早 gate；vul crash 仍不停止 |
| 07 | 反方向部分实现 | 两模式已落地，但硬锁 WRITE/BASH/submit，step 12 强转，prompt 仍 corpus ALWAYS/fake proximity |
| 08 | 未开始 | tests 有 3 fail；源码与 bundled copy 多处不同；QitOS 自身 dirty |

因此实施顺序不变，但任务 04/06/07 的第一步是**删除或替换当前不可靠逻辑**，不是继续叠加新状态。

### 当前工作树的止血顺序

因为未提交两模式改动已经引入 state 污染和 PoC 硬锁，允许在完整依赖链前先做两个严格受限的 hotfix：

```text
01 Result Contract
  -> 06a no-crash 不改 gate + vul-crash stop
  -> 07a observation 纯函数 + 删除 chain WRITE/BASH/submit 硬锁
  -> 回到 02 -> 03 -> 04 -> 05 -> 06b -> 07b -> 08
```

`06a/07a` 只做对应文档“最新再审计状态”列出的删除/止血项，不提前新增完整 schema 或重构 prompt。

## 不可突破的范围

V2 **不新增也不依赖**：

- 本地 fuzzing（libFuzzer、AFL、honggfuzz、coverage-guided search 等）；
- GDB、LLDB、rr、动态调试器；
- 本地 sanitizer/coverage instrumentation；
- 本地构建并运行目标程序；
- Docker/microVM/fresh-container verifier；
- 外部 OSS-Fuzz corpus、历史成功 PoC 或联网下载的 seed；
- 新的 tree-sitter/ctags 等重依赖；
- 新的多 agent 编排层。

V2 允许并应充分复用：

- `READ`、`GREP`、`RepoMap`、`FindSymbols`、`CallsiteSearch`；
- `BASH` 做确定性搜索、文件生成、哈希、`file`/`xxd` 等字节检查；
- 现有 `repo_index.py`、`task_spec.py`、`HarnessMixin` 和格式 toolbox；
- task workspace 内已有的 tests/fixtures/corpus，但必须记录来源并做静态资格检查；
- 官方 `submit_poc`，它是唯一动态 oracle。

## 总体原则

1. 收紧现有协议和模型，不另造一套平行框架。
2. 不可靠信息保留 `unknown`，不通过 heuristic 填满 state。
3. `confirmed` 必须有现有工具结果或源码 span 作为 evidence。
4. `no_crash` 只否定本次 candidate claim，不自动推断 path/gate。
5. candidate 身份由文件 bytes 的 SHA-256 决定，不由路径或文字描述决定。
6. `submit_poc` 串行；在 result contract 修复前禁止任何并行提交。
7. public vul-side crash 后立即停止并保存首次 crash PoC；fix-side 只用于离线评分，不能泄漏到 agent。
8. 源码仓库是开发真相源；bundled QitOS copy 只能通过同步脚本更新。

## 任务依赖图

```text
00 边界与基线
 |
 +--> 01 Result Contract -----------+
 |                                  |
 +--> 02 Bootstrap Precision --> 03 Static Scope/Harness --> 04 Evidence Chain
                                                       \        /
                                                        05 Candidate Experiment
                                                                 |
                                                        06 Oracle/Stop
                                                                 |
                                                        07 Control/Prompt
                                                                 |
                                                        08 Evaluation/Sync
```

## 执行顺序

| 顺序 | 任务 | 交付物 | 独立验收 |
|---:|---|---|---|
| 0 | [00 边界与基线](00-boundaries-and-baseline.md) | 固定范围、基线和不变量 | 基线结果被记录，禁区进入开发说明 |
| 1 | [01 Result Contract](01-result-contract-and-serial-submit.md) | action-id 结果关联、串行 submit | 并行 READ 不串线；多 submit 不并发 |
| 2 | [02 Bootstrap Precision](02-bootstrap-precision.md) | 高精度 anchor/format 初始判断 | `behavior/type/target` 不再误判 AVI/PE/TAR |
| 3 | [03 Static Scope/Harness](03-static-scope-and-harness-contract.md) | 静态 `ScopeMap`、`HarnessContract` | 能解释 entry、input mode、证据或 unknown |
| 4 | [04 Evidence Chain](04-evidence-backed-chain.md) | 证据化 node/gate/claim | 模型不能无 evidence 自称 confirmed |
| 5 | [05 Candidate Experiment](05-candidate-experiment-contract.md) | bytes identity、parent/claim/delta | candidate-result 原子可追溯 |
| 6 | [06 Oracle/Stop](06-oracle-feedback-and-stop.md) | 保守反馈分类、vul-crash stop | no-crash 不污染 gate；首次 crash 停止 |
| 7 | [07 Control/Prompt](07-control-plane-and-prompts.md) | 两模式收敛、软压力、紧凑 prompt | render 纯函数；READ/构造不会互锁 |
| 8 | [08 Evaluation/Sync](08-evaluation-sync-and-rollout.md) | 分层指标、固定 manifest、同步检查 | 同模型 ablation 可归因；两份源码一致 |

状态词定义：

- `未开始`：现有代码没有满足 task 的验收不变量；
- `部分完成`：可复用骨架已出现，但不能跳过 task；
- `反方向部分实现`：同一领域已有改动，但其语义违反本设计，必须先替换；
- `完成`：只有定向测试和全套测试通过后才能标记。

## Coding agent 通用执行协议

每个任务开始前：

```bash
git status --short
git diff --stat
```

- 当前工作树可能含用户正在进行的 `phase/state/observation/validation` 改动；不得覆盖、恢复或重写这些改动。
- 先读任务列出的“复用点”，再新增函数。发现已有同义函数时，扩展已有函数并更新调用方。
- 一次只实现一个任务。不要顺手完成后续任务，不做无关格式化。
- 测试先落地；实现后先运行定向测试，再运行全套测试。
- 不提交 `.agent/`、`.cybergym/`、`.pytest_cache/` 或任何 `poc_*`。

开发目录快速验证：

```bash
PYTHONPATH=../qitos:.. python -m pytest tests -q
```

涉及 QitOS core 时同时运行对应 `../qitos/tests/` 定向测试。准备发布时再按 [08](08-evaluation-sync-and-rollout.md) 执行 canonical tests 和 `scripts/sync_to_qitos.sh`。

## 全局 Definition of Done

V2 完成必须同时满足：

- 所有工具结果可通过 `action_id` 唯一关联；不存在 global/last result fallback；
- `submit_poc` 不可并发；
- description heuristic 不再产生已知短子串误判；
- harness 未知时明确显示 unknown，不伪造 file/stdin/buffer；
- chain/gate 的 confirmed 状态都有 evidence ref；
- candidate bytes、claim、parent、delta、submit result 可闭环追踪；
- no-crash 不产生 fake proximity 或自动 refute；
- vul-side crash 立即成功停止，并保留首次 crash artifact；
- 没有新增本地 fuzz/debug/build/run 路径；
- 源码与 bundled QitOS copy 同步，测试通过；
- 固定模型与 manifest 的对照运行至少没有破坏 baseline 中间指标。
