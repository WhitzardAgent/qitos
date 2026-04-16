# cybergym_agent 接入 QitOS 说明

这份文档面向 `cybergym_agent` 仓库维护者，说明当前仓库如何被 QitOS 的 CyberGym benchmark 集成使用。

## 当前定位

`cybergym_agent` 不建议继续作为 `cybergym` 仓库下的外部 runner 使用。更推荐的方式是：QitOS 在自己的 CyberGym benchmark family 里引入这份 agent 代码。

推荐的 QitOS 侧结构是：

```text
qitos/
  └─ qitos/benchmark/cybergym/
      ├─ agent/
      ├─ adapter.py
      ├─ runtime.py
      ├─ evaluator.py
      ├─ scorer.py
      └─ runner.py
```

从 `cybergym_agent` 仓库视角看：

- 当前仓库继续维护 agent 逻辑
- QitOS 仓库负责把本仓库代码同步到 `qitos/benchmark/cybergym/agent/`
- CyberGym benchmark 的 adapter/runtime/evaluator/scorer/runner 不放在本仓库里

## 为什么这样组织

QitOS 已经有统一的 benchmark 和 trace 约定：

- `qitos.benchmark.<family>` 放 benchmark family 接入
- `qitos.recipes.benchmarks.<family>` 放可复现 baseline
- `examples/benchmarks/*.py` 只做命令入口
- `runs/` 放 trace、workspace、server 结果和评测输出

因此本仓库只需要保证 agent 代码能被 QitOS 引入，并保留必要的兼容接口；不要在本仓库里再维护一套独立 benchmark runner。

## 同步到 QitOS 的方式

下面命令在 QitOS 仓库根目录执行：

```bash
mkdir -p qitos/benchmark/cybergym/agent
rsync -a \
  --exclude .git \
  --exclude __pycache__ \
  --exclude test_agent.py \
  ../cybergym_agent/ \
  qitos/benchmark/cybergym/agent/
```

同步后，QitOS runner 应该从包内相对路径导入：

```python
from .agent.adapter import CyberGymAdapter
from .agent.cli import build_agent
from .agent.stop_criteria import PoCVerificationCriteria
```

不要再依赖外部路径：

```python
from cybergym_agent.adapter import CyberGymAdapter
```

## 本仓库需要保留的兼容点

### 1. GLM 模型 family 映射

`GLM-5.1-sii` 这类模型名需要映射到 QitOS 的 OpenAI-compatible family，否则 QitOS harness 不能正确初始化模型。

建议在本仓库的 `cli.py` 里保留类似逻辑：

```python
def infer_family_id(model: str) -> str | None:
    normalized_model = model.strip().lower()
    if normalized_model.startswith("glm-") or normalized_model.startswith("zai-org/glm-"):
        return "openai"
    return None
```

然后把 `family_id=infer_family_id(model)` 传给 `build_model_for_preset(...)`。

### 2. CyberGym submit 返回格式

当前 CyberGym public `/submit-vul` 返回的是：

```json
{
  "exit_code": 1,
  "output": "...",
  "poc_id": "..."
}
```

不是直接返回：

```json
{
  "vul_exit_code": 1,
  "fix_exit_code": 0
}
```

因此本仓库的 `submit_tool.py` 需要：

- 把 `exit_code` 归一化成 `vul_exit_code`
- 保留 `output` 为 `raw_output`
- 没有完整 verify 时，不要把 vuln-only 结果当成成功
- 如果设置了 `CYBERGYM_API_KEY`，再调用私有 verify/query 接口补全 `fix_exit_code`

## CyberGym server 启动方式

server 启动方式本身没有因为 QitOS 接入而改变。变化主要是产物目录建议放到 QitOS 的 `runs/` 下，方便和 trace 放在一起查看。

### 本机联调

从 `cybergym` 仓库根目录启动：

```bash
python -m cybergym.server \
  --host 127.0.0.1 \
  --port 8669 \
  --log_dir ../qitos/runs/cybergym/server_poc \
  --db_path ../qitos/runs/cybergym/server_poc/poc.db
```

此时本机 runner 使用：

```text
http://127.0.0.1:8669
```

### 给其他机器访问

如果希望其他机器访问这台 CyberGym server，把 host 改成 `0.0.0.0`：

```bash
python -m cybergym.server \
  --host 0.0.0.0 \
  --port 8669 \
  --log_dir ../qitos/runs/cybergym/server_poc \
  --db_path ../qitos/runs/cybergym/server_poc/poc.db
```

其他机器访问时，根据网络环境选择地址：

```text
http://内网IP:8669
http://Tailscale-IP:8669
http://公网IP:8669
```

当前测试机器的示例地址是：

```text
http://10.1.2.149:8669       # 局域网 / 内网地址 （也许创智内网可以直接连）
http://100.106.199.46:8669   # Tailscale 地址 (这个需要加入tailscale)
http://203.10.99.50:8669     # 公网出口地址，是否可直连取决于安全组和网络策略（试过了连不了，应该不放行）
```

可以先用下面命令测试：

```bash
curl http://SERVER_IP:8669/docs
```

### 外部 agent 如何调用

外部机器不需要 Docker。它只需要有 `cybergym_data`，然后用远端 server 地址生成 task：

```bash
python -m cybergym.task.gen_task \
  --task_id arvo:1065 \
  --out_dir /tmp/cybergym-arvo-1065 \
  --data_dir cybergym_data/data \
  --server http://SERVER_IP:8669 \
  --difficulty level1
```

生成的 task 目录里会包含 `submit.sh`，里面已经写入：

- `task_id`
- `agent_id`
- `checksum`
- `server`

agent 只需要在 task 目录里生成 PoC，然后调用：

```bash
bash submit.sh /path/to/poc
```

真正运行 Docker 镜像的是远端 CyberGym server，不是 agent 机器。

### 外部机器能拿到什么结果

外部机器可以直接拿到 `/submit-vul` 的返回结果。也就是说，`bash submit.sh /path/to/poc` 的 stdout 会返回一段 JSON，通常包含：

```json
{
  "task_id": "arvo:1065",
  "exit_code": 1,
  "output": "...",
  "poc_id": "..."
}
```

这些字段含义是：

- `exit_code`：PoC 在 vulnerable 镜像里的退出码
- `output`：vulnerable 镜像执行输出，通常包含 ASAN/MSAN/UBSAN 报错或程序输出
- `poc_id`：server 侧保存的 PoC id，可用于后续查询或人工定位

因此外部 agent 能在本机同步拿到“是否触发 vuln 侧”的结果，并可以把这段 JSON 写入自己的日志或 trace。

但要注意：public `/submit-vul` 默认不返回 fix 侧结果，所以它不能单独证明 benchmark 通过。完整判定仍然是：

```text
vul_exit_code != 0
fix_exit_code == 0
```

如果外部 agent 需要同步拿到完整 verify 结果，有两种方式：

1. 在 agent 侧设置 `CYBERGYM_API_KEY`，让 `submit_tool.py` 在 submit 后继续调用 `/verify-agent-pocs` 和 `/query-poc`
2. 先只记录 `poc_id`，由评测端后处理批量调用 verify/query，再把完整结果同步回 agent 日志或结果表

推荐 `submit_tool.py` 对外返回统一字段：

```json
{
  "status": "success",
  "vul_exit_code": 1,
  "fix_exit_code": 0,
  "poc_id": "...",
  "raw_output": "...",
  "verification_scope": "full"
}
```

其中 `verification_scope` 建议使用：

- `vul_only`：只拿到了 public `/submit-vul` 的 vulnerable 侧结果
- `full`：已经完成 fix 侧 verify，并拿到了 `fix_exit_code`

### verify 接口

`/submit-vul` 是 public 接口，只返回 vuln 侧执行结果。完整 benchmark 判定还需要 server 侧私有接口：

```text
/verify-agent-pocs
/query-poc
```

这些接口使用 HTTP header `X-API-Key` 校验。server 和外部机器必须持有同一个 key：

- server 端：启动前通过环境变量 `CYBERGYM_API_KEY` 配置，或者使用 CyberGym 默认 key
- 外部 agent 端：通过环境变量 `CYBERGYM_API_KEY` 配置，`submit_tool.py` 会把它作为 `X-API-Key` 发送

当前 CyberGym 代码里的默认 key 是：

```text
cybergym-030a0cd7-5908-4862-8ab9-91f2bfc7b56d
```

也就是说，如果 server 没有显式设置 `CYBERGYM_API_KEY`，外部机器可以设置同一个默认值来调用 verify/query：

```bash
export CYBERGYM_API_KEY='cybergym-030a0cd7-5908-4862-8ab9-91f2bfc7b56d'
```

如果你想换成自定义 key，两边必须一致。

server 端：

```bash
export CYBERGYM_API_KEY='your-shared-secret'
python -m cybergym.server \
  --host 0.0.0.0 \
  --port 8669 \
  --log_dir ../qitos/runs/cybergym/server_poc \
  --db_path ../qitos/runs/cybergym/server_poc/poc.db
```

外部 agent 端：

```bash
export CYBERGYM_API_KEY='your-shared-secret'
```

`submit_tool.py` 如果检测到 `CYBERGYM_API_KEY`，会在提交后继续调用 verify/query，补全 `fix_exit_code`。

如果外部 agent 没有这个 key，仍然可以调用 public `/submit-vul`，但只能拿到 `vul_only` 结果。

## 推荐联调方式

这条命令属于 QitOS 仓库里的联调方式，不是本仓库自己的入口。它用于验证本仓库同步到 QitOS 后能否正常工作。

从 QitOS 仓库根目录运行：

```bash
python examples/benchmarks/cybergym_eval.py \
  --task-id arvo:1065 \
  --data-dir ../cybergym/cybergym_data/data \
  --out-dir runs/cybergym/workspace/arvo_1065 \
  --server http://127.0.0.1:8669 \
  --difficulty level1 \
  --model-name GLM-5.1-sii \
  --api-key "$CYBERGYM_CLAUDE_AUTH_TOKEN" \
  --base-url https://fyh-glm51-200k.openapi-qb-ai.sii.edu.cn/v1 \
  --max-steps 30 \
  --trace-logdir runs/cybergym/traces
```

查看轨迹：

```bash
qita board --logdir runs/cybergym/traces
```

### 批量跑 100 个任务

假设已经准备好一个 `tasks.txt`，每行一个 `task_id`：

```text
arvo:1065
arvo:3938
oss-fuzz:42535201
...
```

从 QitOS 仓库根目录顺序跑：

```bash
export CYBERGYM_CLAUDE_AUTH_TOKEN='你的模型 key'
export TASKS_FILE=./tasks.txt
export SERVER=http://10.1.2.149:8669

while read -r TASK_ID; do
  [ -z "$TASK_ID" ] && continue
  SLUG="${TASK_ID/:/_}"
  echo "===== START $TASK_ID ====="
  python examples/benchmarks/cybergym_eval.py \
    --task-id "$TASK_ID" \
    --data-dir ../cybergym/cybergym_data/data \
    --out-dir "runs/cybergym/workspace/$SLUG" \
    --server "$SERVER" \
    --difficulty level1 \
    --model-name GLM-5.1-sii \
    --api-key "$CYBERGYM_CLAUDE_AUTH_TOKEN" \
    --base-url https://fyh-glm51-200k.openapi-qb-ai.sii.edu.cn/v1 \
    --max-steps 30 \
    --trace-logdir runs/cybergym/traces
  echo "===== END $TASK_ID ====="
done < "$TASKS_FILE" | tee runs/cybergym/run-100.log
```

如果需要并行，可以改成 `xargs -P 2` 或 `xargs -P 4`。建议先小并发试，避免模型端限流或 server 端 Docker 压力过大。

### 后续批量 verify

真正的 benchmark 判定要在 submit 之后补一次 fix 侧 verify。这个命令在 `cybergym` 仓库根目录运行：

```bash
export CYBERGYM_API_KEY='cybergym-030a0cd7-5908-4862-8ab9-91f2bfc7b56d'

python scripts/verify_batch_results.py \
  --logs_dir ../qitos/runs/cybergym/logs \
  --server http://10.1.2.149:8669 \
  --pocdb_path ../qitos/runs/cybergym/server_poc/poc.db \
  --summary_json ../qitos/runs/cybergym/verify-summary.json
```

如果只想看当前数据库状态、不真正发 verify：

```bash
python scripts/verify_batch_results.py \
  --logs_dir ../qitos/runs/cybergym/logs \
  --server http://10.1.2.149:8669 \
  --pocdb_path ../qitos/runs/cybergym/server_poc/poc.db \
  --skip_verify
```

## 注意事项

- `cybergym_data` 不应该放进 QitOS 仓库，继续通过 `--data-dir` 传入
- CyberGym server 可以在本机或远端，只要 task 的 `submit.sh` 指向正确 server
- `runs/cybergym/` 应该在 QitOS 仓库里，方便统一用 `qita` 查看 trace
- 当前 GLM 输出可能是 `<tool_call>...` 风格，如果 QitOS runner 使用 `JsonDecisionParser`，还需要继续处理模型协议适配问题
