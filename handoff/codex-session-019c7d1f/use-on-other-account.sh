#!/usr/bin/env bash
set -euo pipefail
SESSION_ID="019c7d1f-400d-7e40-a12a-c44886704897"
PROJ="/Users/pedromousinho/Downloads/dbde-ai-v7-patched"
SRC_DIR="/Users/pedromousinho/Downloads/dbde-ai-v7-patched/handoff/codex-session-019c7d1f"
DST_DIR="$HOME/.codex/sessions/2026/02/20"
FILE="rollout-2026-02-20T22-15-25-019c7d1f-400d-7e40-a12a-c44886704897.jsonl"

mkdir -p "$DST_DIR"
cp -f "$SRC_DIR/$FILE" "$DST_DIR/$FILE"

cd "$PROJ"
if codex resume "$SESSION_ID" -C "$PROJ"; then
  exit 0
fi

echo "Resume falhou nesta conta. Usa o fallback: abrir novo chat e colar START-PROMPT-PT.md"
