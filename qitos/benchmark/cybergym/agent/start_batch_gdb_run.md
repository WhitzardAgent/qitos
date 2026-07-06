# CyberGym v14 GDB：149 上启动 8 组实验

本文是 `start_batch_run.md` 的 v14/GDB 版。目标是在 149 上同时启动 8 个相互隔离的实验组，每组 100 个 Level 1 任务：原有 5 组各 100 个任务，再从 `others.txt` 中按固定随机种子抽取 3 个互不重叠的 100-task 组。

## 1. 固定运行配置

| 项目 | 149 配置 |
|---|---|
| Host | `pgroup@10.1.2.149` |
| SSH key | `/Users/morinop/Desktop/traj_analyzer/pgroup_rsa` |
| 工作区 | `/data/pxd-team/workspace/jcy/cyber-agent` |
| agent 源码 | `/data/pxd-team/workspace/jcy/cyber-agent/cybergym_agent` |
| QitOS | `/data/pxd-team/workspace/jcy/cyber-agent/qitos` |
| CyberGym | `/data/pxd-team/workspace-149/zwq/cybergym` |
| Python | `/data3t/conda_envs/cybergym/bin/python3`，必须为 Python 3.12+ |
| binary 数据 | `/data3t/cybergym-bin/cybergym-server-data` |
| Docker image | `cage/claude-code:cyberdebug` |
| 模型 | `GLM-5.1` |
| 单任务时限 | `14400` 秒 |
| 单组并发 | `4` |
| 端口 | `6441`–`6448` |

不要使用 149 的 `/usr/bin/python3`；它是 Python 3.10，当前 CyberGym 依赖 `enum.StrEnum`。

## 2. 8 组与 endpoint 分配

将真实 URL 填入三个占位符。分配必须保持 4/2/2：

| 组 | 任务文件 | Endpoint |
|---|---|---|
| `v14-gdb-v1-luke` | `v1_luke.txt` | `https://o89mbdpaameoceb5jogodkgegdj95hpe.openapi-qb-ai.sii.edu.cn` |
| `v14-gdb-v2-vader` | `v2_vader.txt` | `https://o89mbdpaameoceb5jogodkgegdj95hpe.openapi-qb-ai.sii.edu.cn` |
| `v14-gdb-v3-rey` | `v3_rey.txt` | `https://o89mbdpaameoceb5jogodkgegdj95hpe.openapi-qb-ai.sii.edu.cn` |
| `v14-gdb-v4-leia` | `v4_leia.txt` | `https://o89mbdpaameoceb5jogodkgegdj95hpe.openapi-qb-ai.sii.edu.cn` |
| `v14-gdb-v5-yoda` | `v5_yoda.txt` | `https://cqgdaj88emgpchb5mmkomqkgkaepd9dh.openapi-qb-ai.sii.edu.cn` |
| `v14-gdb-others-a` | `others_a.txt` | `https://cqgdaj88emgpchb5mmkomqkgkaepd9dh.openapi-qb-ai.sii.edu.cn` |
| `v14-gdb-others-b` | `others_b.txt` | `https://dpj9pphbcmboc9gmkjdamkmpecbd5b8o.openapi-qb-ai.sii.edu.cn` |
| `v14-gdb-others-c` | `others_c.txt` | `https://dpj9pphbcmboc9gmkjdamkmpecbd5b8o.openapi-qb-ai.sii.edu.cn` |

模型 key 使用 `9ugaKOuuVsR/luU1QFEoEpm+KOvDHt+m+A/pdbNrgvo=`。grading key 使用 `cybergym-030a0cd7-5908-4862-8ab9-91f2bfc7b56d`；两者不能混用。grading server 与 agent 侧的 `CYBERGYM_API_KEY` 必须完全一致。

## 3. 更新并同步 v14

```bash
SSH_KEY=/Users/morinop/Desktop/traj_analyzer/pgroup_rsa
HOST=pgroup@10.1.2.149

ssh -i "$SSH_KEY" "$HOST" '
set -euo pipefail
ROOT=/data/pxd-team/workspace/jcy/cyber-agent
PY=/data3t/conda_envs/cybergym/bin/python3

"$PY" --version
test -d /data3t/cybergym-bin/cybergym-server-data
docker image inspect cage/claude-code:cyberdebug >/dev/null

cd "$ROOT/cybergym_agent"
git -c http.proxy= -c https.proxy= pull --ff-only origin para_action
QITOS_ROOT="$ROOT/qitos" bash scripts/sync_to_qitos.sh

PYTHONPATH="$ROOT:$ROOT/qitos" "$PY" - <<"PY"
from cybergym_agent.agent import CyberGymAgent
from cybergym_agent.agent_impl.prompt.prompt_resources import prompt_resource
assert hasattr(CyberGymAgent, "_preserved_project_memory")
assert "submit_poc" in prompt_resource(
    "procedure_memory/candidate_action_templates.md"
)
print("v14 import and prompt resources: OK")
PY
'
```

## 4. 生成 8 个互不重叠的任务文件

原始任务文件目录：

```text
/Users/morinop/Desktop/traj_analyzer/cybergym_workspace/cyber_ladder
```

以下脚本复制前 5 组，并从 `others.txt` 使用固定 seed 抽取 300 个互不重复任务，再切成 3 组。不要分别调用三次 `random.sample`，否则组间可能重叠。

```bash
TASK_SOURCE=/Users/morinop/Desktop/traj_analyzer/cybergym_workspace/cyber_ladder
TASK_BUILD=/tmp/v14_gdb_8x100
rm -rf "$TASK_BUILD"
mkdir -p "$TASK_BUILD"

for f in v1_luke.txt v2_vader.txt v3_rey.txt v4_leia.txt v5_yoda.txt; do
  head -100 "$TASK_SOURCE/$f" > "$TASK_BUILD/$f"
done

python3 - "$TASK_SOURCE/others.txt" "$TASK_BUILD" <<'PY'
from pathlib import Path
import random
import sys

source = Path(sys.argv[1])
out = Path(sys.argv[2])
tasks = list(dict.fromkeys(
    line.strip() for line in source.read_text().splitlines() if line.strip()
))
if len(tasks) < 300:
    raise SystemExit(f"others.txt only has {len(tasks)} unique tasks")

picked = random.Random(20260705).sample(tasks, 300)
for index, suffix in enumerate(("a", "b", "c")):
    group = picked[index * 100:(index + 1) * 100]
    (out / f"others_{suffix}.txt").write_text("\n".join(group) + "\n")
PY

wc -l "$TASK_BUILD"/*.txt
test "$(cat "$TASK_BUILD"/*.txt | wc -l | tr -d ' ')" = 800
test -z "$(cat "$TASK_BUILD"/others_*.txt | sort | uniq -d)"
```

注：前 5 组之间是否有重叠由原始任务集定义；上面的强校验保证新增的 3 个 `others` 组彼此不重叠。

## 5. 创建远端运行目录并上传任务

```bash
RUNS=/data/pxd-team/workspace/jcy/cyber-agent/runs

declare -A TASK_FILE=(
  [v14-gdb-v1-luke]=v1_luke.txt
  [v14-gdb-v2-vader]=v2_vader.txt
  [v14-gdb-v3-rey]=v3_rey.txt
  [v14-gdb-v4-leia]=v4_leia.txt
  [v14-gdb-v5-yoda]=v5_yoda.txt
  [v14-gdb-others-a]=others_a.txt
  [v14-gdb-others-b]=others_b.txt
  [v14-gdb-others-c]=others_c.txt
)

for group in "${!TASK_FILE[@]}"; do
  ssh -i "$SSH_KEY" "$HOST" "mkdir -p '$RUNS/$group'"
  scp -i "$SSH_KEY" \
    "$TASK_BUILD/${TASK_FILE[$group]}" \
    "$HOST:$RUNS/$group/${TASK_FILE[$group]}"
done
```

## 6. 生成每组 launch.sh

使用已经通过单任务验证的 launch 作为模板：

```text
/data/pxd-team/workspace/jcy/cyber-agent/runs/v14_smoke_0705_149_don/v14smoke149-g00/launch.sh
```

如果该 smoke 目录已归档，应先把这份模板复制到稳定位置，例如：

```text
/data/pxd-team/workspace/jcy/cyber-agent/templates/launch_v14_gdb_149.sh
```

执行下面的生成脚本前，替换三个 endpoint 和两个 key 占位符：

```bash
API_ENDPOINT_A='https://o89mbdpaameoceb5jogodkgegdj95hpe.openapi-qb-ai.sii.edu.cn'
API_ENDPOINT_B='https://cqgdaj88emgpchb5mmkomqkgkaepd9dh.openapi-qb-ai.sii.edu.cn'
API_ENDPOINT_C='https://dpj9pphbcmboc9gmkjdamkmpecbd5b8o.openapi-qb-ai.sii.edu.cn'
LLM_API_KEY='9ugaKOuuVsR/luU1QFEoEpm+KOvDHt+m+A/pdbNrgvo='
CYBERGYM_GRADING_KEY='cybergym-030a0cd7-5908-4862-8ab9-91f2bfc7b56d'
TEMPLATE=/data/pxd-team/workspace/jcy/cyber-agent/runs/v14_smoke_0705_149_don/v14smoke149-g00/launch.sh

groups=(
  v14-gdb-v1-luke v14-gdb-v2-vader v14-gdb-v3-rey v14-gdb-v4-leia
  v14-gdb-v5-yoda v14-gdb-others-a v14-gdb-others-b v14-gdb-others-c
)
tasks=(
  v1_luke.txt v2_vader.txt v3_rey.txt v4_leia.txt
  v5_yoda.txt others_a.txt others_b.txt others_c.txt
)
endpoints=(
  "$API_ENDPOINT_A" "$API_ENDPOINT_A" "$API_ENDPOINT_A" "$API_ENDPOINT_A"
  "$API_ENDPOINT_B" "$API_ENDPOINT_B" "$API_ENDPOINT_C" "$API_ENDPOINT_C"
)

for i in "${!groups[@]}"; do
  group=${groups[$i]}
  task=${tasks[$i]}
  endpoint=${endpoints[$i]}
  port=$((6441 + i))
  run_root="$RUNS/$group"

  ssh -i "$SSH_KEY" "$HOST" bash -s -- \
    "$TEMPLATE" "$run_root" "$group" "$task" "$endpoint" "$port" \
    "$LLM_API_KEY" "$CYBERGYM_GRADING_KEY" <<'REMOTE'
set -euo pipefail
template=$1; run_root=$2; group=$3; task=$4; endpoint=$5; port=$6
llm_key=$7; grading_key=$8
cp "$template" "$run_root/launch.sh"

sed -i \
  -e "s|^RUN_ROOT=.*|RUN_ROOT=\"$run_root\"|" \
  -e "s|^OUT_DIR=.*|OUT_DIR=\"$run_root\"|" \
  -e "s|^TASK_FILE=.*|TASK_FILE=\"$run_root/$task\"|" \
  -e 's|^PYTHON_BIN=.*|PYTHON_BIN="/data3t/conda_envs/cybergym/bin/python3"|' \
  -e 's|^BINARY_DIR=.*|BINARY_DIR="/data3t/cybergym-bin/cybergym-server-data"|' \
  -e "s|^SERVER_PORT=.*|SERVER_PORT=\"$port\"|" \
  -e "s|^SERVER_URL=.*|SERVER_URL=\"http://127.0.0.1:$port\"|" \
  -e "s|^BASE_URL=.*|BASE_URL=\"$endpoint\"|" \
  -e "s|^API_KEY=.*|API_KEY=\"$llm_key\"|" \
  -e "s|^LLM_KEY=.*|LLM_KEY=\"$llm_key\"|" \
  -e "s|^GRADING_KEY=.*|GRADING_KEY=\"$grading_key\"|" \
  -e 's|^CONCURRENCY=.*|CONCURRENCY="4"|' \
  -e 's|^MAX_STEPS=.*|MAX_STEPS="1000000"|' \
  -e 's|^MAX_RUNTIME_SECONDS=.*|MAX_RUNTIME_SECONDS="14400"|' \
  -e "s|^TMUX_SESSION=.*|TMUX_SESSION=\"jcy-$group\"|" \
  -e "s|^TRACE_PREFIX=.*|TRACE_PREFIX=\"qitos_${group}_glm-51\"|" \
  -e "s|^OUTPUT_JSONL=.*|OUTPUT_JSONL=\"\${OUT_DIR}/cybergym_${group}.jsonl\"|" \
  -e 's|export CYBERGYM_ENABLE_DYNAMIC_TOOLS=.*|export CYBERGYM_ENABLE_DYNAMIC_TOOLS=1|' \
  -e 's|export CYBERGYM_STAGE_VUL_BINARY=.*|export CYBERGYM_STAGE_VUL_BINARY=1|' \
  "$run_root/launch.sh"

chmod +x "$run_root/launch.sh"

# 启动前硬门禁：避免 server 监听一个端口、batch 却提交到另一个端口。
grep -q "^SERVER_PORT=\"$port\"" "$run_root/launch.sh"
grep -q "^SERVER_URL=\"http://127.0.0.1:$port\"" "$run_root/launch.sh"
grep -q '^PYTHON_BIN="/data3t/conda_envs/cybergym/bin/python3"' "$run_root/launch.sh"
grep -q '^BINARY_DIR="/data3t/cybergym-bin/cybergym-server-data"' "$run_root/launch.sh"
grep -q 'CYBERGYM_ENABLE_DYNAMIC_TOOLS=1' "$run_root/launch.sh"
grep -q 'CYBERGYM_STAGE_VUL_BINARY=1' "$run_root/launch.sh"
grep -q 'CYBERGYM_BINARY_DIR="${BINARY_DIR}"' "$run_root/launch.sh"
test "$(wc -l < "$run_root/$task")" = 100
REMOTE
done
```

如果模板没有显式的以下三行，不要启动：

```bash
export CYBERGYM_ENABLE_DYNAMIC_TOOLS=1
export CYBERGYM_STAGE_VUL_BINARY=1
export CYBERGYM_BINARY_DIR="${BINARY_DIR}"
```

## 7. 启动前总预检

```bash
ssh -i "$SSH_KEY" "$HOST" '
set -euo pipefail

echo "load:"; uptime
echo "memory:"; free -h
echo "containers:"; docker ps -q | wc -l

for port in $(seq 6441 6448); do
  if ss -ltn | grep -q ":${port} "; then
    echo "ERROR: port ${port} already in use" >&2
    exit 1
  fi
done

for group in \
  v14-gdb-v1-luke v14-gdb-v2-vader v14-gdb-v3-rey v14-gdb-v4-leia \
  v14-gdb-v5-yoda v14-gdb-others-a v14-gdb-others-b v14-gdb-others-c; do
  test -x "/data/pxd-team/workspace/jcy/cyber-agent/runs/$group/launch.sh"
done
'
```

149 上启动 8 组、每组并发 4，最多会新增约 32 个容器。即使要求同时启动，也应先看 load/memory；若机器已有重负载，可以仍然启动 8 个 tmux 实验，但把每组 `CONCURRENCY` 暂时降为 2。

## 8. 同时启动 8 组

```bash
for group in \
  v14-gdb-v1-luke v14-gdb-v2-vader v14-gdb-v3-rey v14-gdb-v4-leia \
  v14-gdb-v5-yoda v14-gdb-others-a v14-gdb-others-b v14-gdb-others-c; do
  ssh -i "$SSH_KEY" "$HOST" \
    "bash '/data/pxd-team/workspace/jcy/cyber-agent/runs/$group/launch.sh' --launch"
  sleep 3
done
```

这里的“同时”指 8 个 tmux 实验全部保持运行；组间用 3 秒错峰，避免 8 个 server 和 sync 同时争抢 NFS。

## 9. 启动后验收

```bash
ssh -i "$SSH_KEY" "$HOST" '
set -euo pipefail

tmux ls | grep "jcy-v14-gdb-"

for port in $(seq 6441 6448); do
  ss -ltn | grep -q ":${port} " || {
    echo "missing grading server on ${port}" >&2
    exit 1
  }
done

for group in \
  v14-gdb-v1-luke v14-gdb-v2-vader v14-gdb-v3-rey v14-gdb-v4-leia \
  v14-gdb-v5-yoda v14-gdb-others-a v14-gdb-others-b v14-gdb-others-c; do
  root="/data/pxd-team/workspace/jcy/cyber-agent/runs/$group"
  echo "=== $group ==="
  pgrep -af "run_cybergym_batch.py.*$group" || true
  grep -n "runtime exception" "$root"/traces/*/tui.log 2>/dev/null | head || true
done
'
```

在至少一个任务进入工具执行后，抽查容器：

```bash
ssh -i "$SSH_KEY" "$HOST" '
cid=$(docker ps --filter name=qitos_ -q | head -1)
test -n "$cid"
docker inspect "$cid" --format "caps={{json .HostConfig.CapAdd}}"
docker inspect "$cid" --format "{{range .Mounts}}{{println .Source \" -> \" .Destination \" rw=\" .RW}}{{end}}"
docker exec "$cid" sh -lc "command -v gdb; ls -l /out | head"
'
```

验收标准：

- `CAP_SYS_PTRACE` 存在；
- `/out/<fuzzer>` 的宿主来源以 `/data3t/cybergym-bin/cybergym-server-data` 开头；
- `/out` 与 `/out-libs` 为只读挂载；
- `tui.log` 中工具总数包含动态工具启用后的增量；
- `tui.log` 无重复 `runtime exception`；
- `submit_poc` 后，对应组的 `server.log` 或 `server_poc/poc.db` 出现该任务记录。

## 10. 监控成绩与拉取轨迹

fix 侧成绩以各组的 `server_poc/poc.db` 为准：

```bash
ssh -i "$SSH_KEY" "$HOST" '
cd /data/pxd-team/workspace/jcy/cyber-agent/runs
python3 tools/verify_view.py \
  v14-gdb-v1-luke v14-gdb-v2-vader v14-gdb-v3-rey v14-gdb-v4-leia \
  v14-gdb-v5-yoda v14-gdb-others-a v14-gdb-others-b v14-gdb-others-c \
  --watch 15
'
```

拉取完整 trace，包括 `tui.log`、`events.jsonl`、`steps.jsonl` 与 `agent_steps/`：

```bash
LOCAL_TRACE=/Users/morinop/Desktop/traj_analyzer/cybergym_workspace/remote_traces_v14_gdb_149
mkdir -p "$LOCAL_TRACE"

for group in \
  v14-gdb-v1-luke v14-gdb-v2-vader v14-gdb-v3-rey v14-gdb-v4-leia \
  v14-gdb-v5-yoda v14-gdb-others-a v14-gdb-others-b v14-gdb-others-c; do
  mkdir -p "$LOCAL_TRACE/$group"
  rsync -az --partial \
    -e "ssh -i $SSH_KEY" \
    "$HOST:/data/pxd-team/workspace/jcy/cyber-agent/runs/$group/traces/" \
    "$LOCAL_TRACE/$group/"
done

find "$LOCAL_TRACE" -name tui.log -size +0 -print | wc -l
```

运行中同步得到的是快照；最终分析前应在任务结束后再执行一次 rsync，确保 `manifest.json`、`events.jsonl` 和 `tui.log` 同步到完整终态。

## 11. 停止全部 8 组

```bash
for group in \
  v14-gdb-v1-luke v14-gdb-v2-vader v14-gdb-v3-rey v14-gdb-v4-leia \
  v14-gdb-v5-yoda v14-gdb-others-a v14-gdb-others-b v14-gdb-others-c; do
  ssh -i "$SSH_KEY" "$HOST" "tmux kill-session -t 'jcy-$group' 2>/dev/null || true"
done
```

