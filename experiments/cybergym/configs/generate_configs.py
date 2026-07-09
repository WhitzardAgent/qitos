#!/usr/bin/env python3
"""Generate all 12 CyberGym experiment YAML configs from the ops manual spec."""
from pathlib import Path

GROUPS = [
    # name,       port, endpoint, task_file,        task_count
    ("v1-luke",    6441, "o89m", "v1_luke.txt",       100),
    ("v2-vader",   6442, "o89m", "v2_vader.txt",      100),
    ("v3-rey",     6443, "o89m", "v3_rey.txt",        100),
    ("v4-leia",    6444, "dpj9", "v4_leia.txt",       100),
    ("v5-yoda",    6445, "cqgd", "v5_yoda.txt",       100),
    ("v6-echo",    6446, "o89m", "v6_echo.txt",       144),
    ("v7-foxtrot", 6447, "o89m", "v7_foxtrot.txt",    144),
    ("v8-golf",    6448, "o89m", "v8_golf.txt",       144),
    ("v9-hotel",   6449, "dpj9", "v9_hotel.txt",      144),
    ("v10-india",  6450, "dpj9", "v10_india.txt",     144),
    ("v11-juliet", 6451, "cqgd", "v11_juliet.txt",    144),
    ("v12-kilo",   6452, "cqgd", "v12_kilo.txt",      143),
]

ENDPOINT_URLS = {
    "o89m": "https://o89mbdpaameoceb5jogodkgegdj95hpe.openapi-qb-ai.sii.edu.cn/v1",
    "dpj9": "https://dpj9pphbcmboc9gmkjdamkmpecbd5b8o.openapi-qb-ai.sii.edu.cn/v1",
    "cqgd": "https://cqgdaj88emgpchb5mmkomqkgkaepd9dh.openapi-qb-ai.sii.edu.cn/v1",
}

TEMPLATE = """\
# CyberGym experiment: {name} ({task_count} tasks, {endpoint} endpoint)
experiment:
  name: "{name}"
  benchmark: "cybergym"
  split: "level1"
  concurrency: 2
  resume: true

model:
  model_name: "GLM-5.1"
  api_key: "${{LLM_API_KEY}}"
  base_url: "{base_url}"

agent:
  max_steps: 1000
  max_runtime_seconds: 7200
  agent_mode: "classic"

environment:
  data_dir: "${{CYBERGYM_DATA_DIR}}"
  server: "http://127.0.0.1:{port}"
  workspace: "runs/${{EXPERIMENT_NAME}}/workspace"
  trace_logdir: "runs/${{EXPERIMENT_NAME}}/traces"
  trace_prefix: "qitos_${{EXPERIMENT_NAME}}_glm-51"
  use_docker: true
  docker_image: "cage/claude-code:cyberdebug"
  stage_vul_binary: true
  binary_dir: "${{CYBERGYM_BINARY_DIR}}"
  grading_key: "${{CYBERGYM_GRADING_KEY}}"

tasks:
  source: "file"
  file: "${{CYBERGYM_AGENT_ROOT}}/cyber_ladder/{task_file}"
  difficulty: "level1"
  limit: {task_count}

output:
  dir: "runs/${{EXPERIMENT_NAME}}"
  filename: "cybergym_{safe_name}.jsonl"

server:
  port: {port}
  binary_dir: "${{CYBERGYM_BINARY_DIR}}"
"""

out_dir = Path(__file__).parent
for name, port, endpoint, task_file, task_count in GROUPS:
    safe_name = name.replace("-", "_")
    yaml_content = TEMPLATE.format(
        name=name,
        port=port,
        endpoint=endpoint,
        task_file=task_file,
        task_count=task_count,
        base_url=ENDPOINT_URLS[endpoint],
        safe_name=safe_name,
    )
    out_path = out_dir / f"{safe_name}.yaml"
    out_path.write_text(yaml_content)
    print(f"  wrote {out_path.name}")

print(f"\nGenerated {len(GROUPS)} configs in {out_dir}")
