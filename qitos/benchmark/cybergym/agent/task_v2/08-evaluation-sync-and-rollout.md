# 08 — 建立可归因评测、同步和发布闭环

优先级：P1  
依赖：01-07 全部完成  
禁止：用单任务成功替代评测；联网 corpus；把 any-crash 冒充 strict score

## 最新再审计状态

状态：**未开始，且发布基线目前不满足**。

当前事实：

- agent tests：10 pass / 3 fail；
- 开发源码和 bundled QitOS agent 在多处受管文件上不同；
- bundled copy 尚无 `agent_prompts/mode/`；
- `../qitos` 另有 4 个未提交 parser/render 文件；
- task_v2 和 issues 文档不属于 bundled runtime，不应被同步脚本复制。

任务 08 必须先区分“agent source sync”与“QitOS 用户已有 dirty changes”。同步脚本只能更新 `qitos/benchmark/cybergym/agent` allowlist，不能触碰 `qitos/engine/parser.py` 或 `qitos/render/*`。

## 目标

复用 QitOS 现有 trace/run-report 基础，建立能定位 prepare/localization/construction/oracle 失败的轻量评测，并确保开发源码与 bundled runtime 不再分叉。

## 必须复用

- `../qitos/qitos/benchmark/cybergym/runner.py`
- `../qitos/scripts/cybergym_run_report.py`
- `../qitos/scripts/cybergym_success_rate.py`
- `agent_impl/observations.py` 已有 step trace/summary
- `agent_impl/exchange_logger.py`
- `scripts/sync_to_qitos.sh`

不要创建第三套 trace format 或新的 benchmark runner。

## 文件

- 修改现有 trace summary producer（优先 `agent_impl/observations.py`/runner metadata）
- 修改 `../qitos/scripts/cybergym_run_report.py`
- 修改对应 `../qitos/tests/test_cybergym_run_report.py`
- 可新增 `scripts/audit_task_spec.py`（若任务 02 未建）
- 修改/新增 `tests/test_trace_metrics.py`
- 必要时修改 `scripts/sync_to_qitos.sh`

## 每次运行必须记录的版本信息

```text
task_id
agent source commit + dirty flag
qitos commit
model id/version
temperature/seed
step/token/time/submit budget
task manifest id
runtime mode
```

dirty run 可以调试，但不得进入正式 ablation 汇总，除非保存 diff hash。

## Stage outcome

每个 run 记录**最早可证实的失败阶段**，无法判断时用 unknown：

```text
PREPARE_ANCHOR_MISS
SCOPE_TARGET_MISS
HARNESS_CONTRACT_MISS
LOCALIZATION_MISS
CLAIM_UNSUPPORTED
NO_CANDIDATE
CANDIDATE_INVALID_OR_DUPLICATE
OFFICIAL_NO_CRASH
SUBMISSION_OR_TIMEOUT_ERROR
VUL_CRASH_SUCCESS
UNKNOWN
```

不要出现 `PATH_NOT_REACHED`、`TRIGGER_DISTANCE` 等 oracle 无法证明的类别。

## 中间指标

### Prepare

- 高置信 symbol/file anchor 数；
- format/strategy unknown rate；
- 已知短子串 false positive 数。

### Scope/Harness

- 是否发现 entry；
- input mode 是否有 evidence；
- root 外 harness 是否命中；
- seed qualification 数和理由。

### Evidence/Claim

- supported node/gate/claim 数；
- 无 evidence promotion 拒绝数；
- target definition 是否被 READ。

### Candidate

- first candidate step；
- candidate 数、unique bytes 数；
- exact-delta rate；
- duplicate prevented；
- claim-bound rate。

### Oracle/Outcome

- submit 次数；
- no-crash/error/timeout/crash；
- first crash step；
- public vul-crash rate；
- 离线 strict evaluator score（单独字段，永不反馈 agent）。

## Trace schema 要求

- 只追加新字段，不破坏旧 manifest reader；
- 每个 metric 有默认值；
- 不把完整 PoC bytes、hidden fix output 或大段 source 放入 summary；
- candidate 只记录 SHA-256、size、claim id、delta summary；
- evidence ref 可重放到 workspace artifact。

## Ablation 规则

按任务依赖逐步比较：

1. baseline；
2. + Result Contract；
3. + Bootstrap Precision；
4. + Static Harness Contract；
5. + Evidence Chain；
6. + Candidate Experiment；
7. + Oracle/Control cleanup。

每次必须固定 model、manifest、budget 和 QitOS commit。至少报告：

- 24-task smoke；
- 失败阶段迁移；
- first candidate step；
- unique submit 数；
- vul-crash 数；
- strict offline score（如果 evaluator 可用）。

不要只展示最好 seed；多随机 seed 时展示均值、分布和逐任务结果。

## 测试层次

### 1. Unit

每个任务文档列出的定向测试。

### 2. Agent 全套

```bash
PYTHONPATH=../qitos:.. python -m pytest tests -q
```

### 3. QitOS 定向

```bash
PYTHONPATH=../qitos:.. python -m pytest \
  ../qitos/tests/test_advanced_tools_and_executor.py \
  ../qitos/tests/test_native_tool_calling_runtime.py \
  ../qitos/tests/test_cybergym_task_spec.py \
  ../qitos/tests/test_cybergym_candidate_failure_records.py \
  ../qitos/tests/test_cybergym_run_report.py -q
```

### 4. Canonical runtime

按 `AGENTS.md`：

```bash
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym \
  python3 -m pytest tests -q
```

## 同步顺序

开发源码测试通过后：

```bash
bash scripts/sync_to_qitos.sh
```

然后：

```bash
cd /data/pxd-team/workspace-149/zwq/qitos-cybergym
PYTHONPATH=/data/pxd-team/workspace-149/zwq/qitos-cybergym \
  python3 -m py_compile qitos/benchmark/cybergym/agent/agent.py
```

同步脚本必须：

- 使用 allowlist/rsync 现有逻辑，不复制 runtime artifact；
- 同步完成后比较源目录与 bundled copy 的受管文件；
- 差异非零时失败；
- 不反向从 QitOS copy 覆盖源码。

## 发布 gate

全部满足才允许进入大样本 benchmark：

- 01 的 result attribution tests 全绿；
- 02 的短子串 false positive 为 0；
- observation render idempotent；
- public crash stop 测试通过；
- 禁区扫描无新增 fuzz/debug/local runner；
- agent/QitOS tests 全绿；
- source/bundled copy 无受管差异；
- 24-task smoke 没有明显降低 harness discovery、first candidate 或 vul-crash 指标。
- 当前三个 baseline failure 已按 [CURRENT_STATE.md](CURRENT_STATE.md) 的归类解决，不允许 xfail/删除掩盖。

## 禁区扫描建议

这只是代码审计，不执行工具：

```bash
rg -n "libFuzzer|AFL|honggfuzz|gdb|lldb|sanitize-coverage|coverage-guided|local verifier" \
  agent.py agent_impl agent_prompts task_spec.py state.py
```

命中注释/禁止说明可接受；任何新增执行路径必须删除。

## 验收

- 报告能区分“没找到 target / 没建立 harness / 没形成 candidate / 官方 no-crash”；
- public vul-crash 与 offline strict score 分栏；
- ablation 固定模型和环境；
- 没有新 runner/trace 轮子；
- bundled copy 与源码一致；
- 无本地 fuzz/debug/build/run 能力进入 V2。
