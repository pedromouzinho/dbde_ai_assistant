#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <runtime_check_url> <bearer_token> [manifest_path]" >&2
  exit 2
fi
RUNTIME_CHECK_URL="$1"
BEARER_TOKEN="$2"
MANIFEST_PATH="${3:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/deploy/runtime-manifest.json}"
if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi
if [[ ! -f "$MANIFEST_PATH" ]]; then
  echo "Manifest not found: $MANIFEST_PATH" >&2
  exit 1
fi
runtime_json="$(curl -fsS -H "Authorization: Bearer $BEARER_TOKEN" "$RUNTIME_CHECK_URL")"
ok=1
while IFS=$'\t' read -r rel expected; do
  [[ -z "$rel" ]] && continue
  actual="$(jq -r --arg rel "$rel" '.files[$rel].sha256_full // empty' <<< "$runtime_json")"
  if [[ -z "$actual" ]]; then
    echo "[FAIL] missing runtime hash for $rel"
    ok=0
    continue
  fi
  if [[ "$actual" != "$expected" ]]; then
    echo "[FAIL] drift $rel"
    echo "  expected: $expected"
    echo "  actual  : $actual"
    ok=0
  else
    echo "[OK] $rel"
  fi
done < <(jq -r '.files | to_entries[] | [.key, .value] | @tsv' "$MANIFEST_PATH")
if [[ $ok -ne 1 ]]; then
  exit 1
fi
echo "Runtime verification passed."
