#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QITOS_ROOT="${QITOS_ROOT:-$(cd "$ROOT_DIR/.." && pwd)/qitos}"
DEST_DIR="${DEST_DIR:-$QITOS_ROOT/qitos/benchmark/cybergym/agent}"

if [[ ! -d "$QITOS_ROOT" ]]; then
  echo "QITOS_ROOT does not exist: $QITOS_ROOT" >&2
  echo "Set QITOS_ROOT explicitly, for example:" >&2
  echo "  QITOS_ROOT=/data/pxd-team/workspace/jcy/cyber-agent/qitos bash scripts/sync_to_qitos.sh" >&2
  exit 1
fi

if [[ ! -d "$QITOS_ROOT/qitos" ]]; then
  echo "QITOS_ROOT does not look like a qitos checkout: $QITOS_ROOT" >&2
  exit 1
fi

case "$DEST_DIR" in
  "$QITOS_ROOT"/qitos/benchmark/cybergym/agent) ;;
  *)
    echo "Refusing to sync to unexpected DEST_DIR: $DEST_DIR" >&2
    echo "Expected: $QITOS_ROOT/qitos/benchmark/cybergym/agent" >&2
    exit 1
    ;;
esac

mkdir -p "$DEST_DIR"

rsync -a --delete \
  --exclude '.git' \
  --exclude '.worktrees' \
  --exclude '.agent' \
  --exclude '.cybergym' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'pocs' \
  --exclude 'qitos' \
  --exclude 'docs' \
  --exclude 'tests' \
  --exclude 'vnext' \
  --exclude 'vnext_*' \
  --exclude 'v14_next' \
  --exclude 'vnext_audit' \
  "$ROOT_DIR"/ \
  "$DEST_DIR"/

legacy_modules=(
  "agent_impl/tool_render.py"
  "agent_impl/tool_registry.py"
  "agent_impl/observations.py"
  "agent_impl/feedback.py"
  "agent_impl/tools.py"
  "agent_impl/task_analysis.py"
)

stale=()
for rel in "${legacy_modules[@]}"; do
  if [[ -e "$DEST_DIR/$rel" ]]; then
    stale+=("$rel")
  fi
done

if (( ${#stale[@]} )); then
  echo "ERROR: stale pre-refactor modules remain in bundled copy:" >&2
  printf '  %s\n' "${stale[@]}" >&2
  exit 1
fi

echo "Synced CyberGym agent source:"
echo "  from: $ROOT_DIR"
echo "  to:   $DEST_DIR"
