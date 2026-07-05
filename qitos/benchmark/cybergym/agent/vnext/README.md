# vNext 实施任务索引

这 5 个任务按“先建立证据，再生成路径，再落到 PoC 字节”的顺序组织。每个任务都可以单独提交 PR，但任务 3、4 必须基于前置任务的数据契约实现，不能临时从 `metadata` 拼接非类型化字段。

共同的 context/prompt 落地规则见 [`CONTEXT_PROMPT_DESIGN.md`](CONTEXT_PROMPT_DESIGN.md)。它是每个任务 Definition of Done 的组成部分，不是额外子任务。

共同的 sink 目标、全量数据分析、漏洞知识规则、TSA 移植优先级和预测见 [`SINK_DISCOVERY_AND_METRICS.md`](SINK_DISCOVERY_AND_METRICS.md)。所有任务必须能解释它如何提高 sink coverage、sink-to-crash conversion，或防止这两类收益回退。

## 执行顺序

```text
05a Freeze baseline + error-stack evaluator
                 ↓
01 Description ─┐
                 ├─> 03 Ranked Paths ─> 04 Input Mapping ─> 05 Cleanup/Rollout
02 Harness ─────┘
```

- 在实现功能前先完成 Task 05 的 evaluator 最小切片：冻结 task manifest、project split、baseline sink/crash 指标。否则后续无法判断改动是否切实有效。
- `01` 与 `02` 可并行。
- `03` 必须等 `01`、`02` 的 state contract 稳定。
- `04` 复用 `03` 的路径和已有 `analyze_sink_candidate()`，不新开分析入口。
- `05` 只在 1–4 的 feature flag 和回归完成后移除旧主路径。

## 任务清单

1. [`01-description-analysis-and-ref-verification.md`](01-description-analysis-and-ref-verification.md)
2. [`02-harness-consumption-model.md`](02-harness-consumption-model.md)
3. [`03-ranked-vulnerability-paths.md`](03-ranked-vulnerability-paths.md)
4. [`04-sink-argument-input-mapping.md`](04-sink-argument-input-mapping.md)
5. [`05-cleanup-evaluation-rollout.md`](05-cleanup-evaluation-rollout.md)

## 共同约束

- 不提交 `.agent/`、`.cybergym/`、`.pytest_cache/` 或 `poc_*`。
- 不把 runtime evidence 写进源码目录中的静态文档。
- 新 state 字段必须兼容 dict 恢复和旧 checkpoint。
- 新分析结果必须区分 `success`、`partial`、`unresolved`；禁止把超时或未解析调用当作 negative evidence。
- 新 prompt 内容必须有条数/字符预算，并有测试锁定结构而不是锁定整段文案。
- `submit_poc` 仍是唯一 oracle；静态分析输出只能是 prior、lead、requirement 或 mapping。
- 新能力必须明确写出：进入六段式 brief 的哪个 section、在哪些 phase 可见、何时衰减、使用什么 provenance、如何影响 Next Action。
- phase prompt 必须教模型消费新信息；不得复制 observation 中的动态 path/ref/mapping。
- 新事实必须能从 typed state 重建，不能依赖未被 snip 的历史 tool output。
- 每个任务必须增加最终 observation 的 end-to-end 测试，而不只测试独立 renderer。
- 每个任务 PR 必须声明主要影响指标：ExactSinkRecall@K、CrashPathRecall@K、CausalCoverage、sink-to-crash conversion 或 context fidelity。
- `error.txt` 只能用于离线评测/规则回归，不得进入 agent Level-1 runtime、state 或 prompt。
