#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 4 ]]; then
  cat >&2 <<'USAGE'
Usage:
  immutable_zip_deploy.sh <kudu_zipdeploy_url> <kudu_auth_basic> <runtime_check_url> <bearer_token>
Example:
  immutable_zip_deploy.sh "https://<app>.scm.azurewebsites.net/api/zipdeploy" "$KUDU_AUTH" "https://dbdeai.pt/api/runtime/check" "$DBDE_TOKEN"
USAGE
  exit 2
fi
KUDU_ZIPDEPLOY_URL="$1"
KUDU_AUTH_BASIC="$2"
RUNTIME_CHECK_URL="$3"
BEARER_TOKEN="$4"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST_PATH="$ROOT/deploy/runtime-manifest.json"
TMP_DIR="$(mktemp -d)"
ZIP_PATH="$TMP_DIR/deploy.zip"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT
"$ROOT/scripts/deploy/build_runtime_manifest.sh" "$MANIFEST_PATH"
(
  cd "$ROOT"
  git ls-files -z | xargs -0 zip -q "$ZIP_PATH"
  EXTRA_FILES=(
    "deploy/runtime-manifest.json"
    "scripts/deploy/build_runtime_manifest.sh"
    "scripts/deploy/verify_runtime_check.sh"
    "scripts/deploy/immutable_zip_deploy.sh"
    "upload_worker.py"
    "export_worker.py"
    "startup_worker.sh"
    "assets/fonts/Montserrat-Regular.ttf"
    "assets/fonts/Montserrat-Bold.ttf"
    "assets/fonts/Montserrat-Italic.ttf"
    "DEPLOY_v7.2.1_IMMUTABLE.md"
  )
  for rel in "${EXTRA_FILES[@]}"; do
    if [[ -f "$rel" ]]; then
      zip -q "$ZIP_PATH" "$rel"
    fi
  done
)
curl -fsS -X POST \
  -H "Authorization: Basic $KUDU_AUTH_BASIC" \
  --data-binary "@$ZIP_PATH" \
  "$KUDU_ZIPDEPLOY_URL" >/dev/null
"$ROOT/scripts/deploy/verify_runtime_check.sh" "$RUNTIME_CHECK_URL" "$BEARER_TOKEN" "$MANIFEST_PATH"
echo "Immutable deploy + runtime verification succeeded."
