#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QITOS_ROOT="${QITOS_ROOT:-$ROOT_DIR/qitos}"
DEST_DIR="${DEST_DIR:-$QITOS_ROOT/qitos/benchmark/cybergym/agent}"

if [[ ! -d "$QITOS_ROOT" ]]; then
  echo "QITOS_ROOT does not exist: $QITOS_ROOT" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"

rsync -a \
  --exclude '.git' \
  --exclude '.worktrees' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude '.cybergym' \
  --exclude 'qitos' \
  --exclude 'docs' \
  --exclude 'tests' \
  "$ROOT_DIR"/ \
  "$DEST_DIR"/

echo "Synced CyberGym agent source:"
echo "  from: $ROOT_DIR"
echo "  to:   $DEST_DIR"
