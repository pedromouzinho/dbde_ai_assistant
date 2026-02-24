#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-$ROOT/deploy/runtime-manifest.json}"
TARGETS=(
  "app.py"
  "agent.py"
  "tools.py"
  "config.py"
  "static/index.html"
)
if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi
files_json='{}'
for rel in "${TARGETS[@]}"; do
  abs="$ROOT/$rel"
  if [[ ! -f "$abs" ]]; then
    echo "Missing target file: $rel" >&2
    exit 1
  fi
  sha="$(shasum -a 256 "$abs" | awk '{print $1}')"
  files_json="$(jq --arg k "$rel" --arg v "$sha" '. + {($k): $v}' <<< "$files_json")"
done
mkdir -p "$(dirname "$OUT")"
app_version="$(awk -F'"' '/^APP_VERSION = / {print $2; exit}' "$ROOT/config.py" || true)"
jq -n \
  --arg generated_at_utc "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
  --arg app_version "$app_version" \
  --argjson files "$files_json" \
  '{generated_at_utc: $generated_at_utc, app_version: $app_version, files: $files}' > "$OUT"
echo "Manifest written: $OUT"
