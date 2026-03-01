#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-millennium-ai-assistant}"
RG="${RG:-rg-MS_Access_Chabot}"
SLOT="${SLOT:-staging}"
PROD_URL="${PROD_URL:-https://dbdeai.pt}"
MAX_WAIT=120
POLL_INTERVAL=5

printf "=== DBDE AI Rollback: swap back production -> staging ===\n\n"

az webapp deployment slot swap \
  --name "$APP_NAME" \
  --resource-group "$RG" \
  --slot "$SLOT" \
  --target-slot production

printf "\n⏳ A aguardar readiness de production...\n"
elapsed=0
status="000"
while [ "$elapsed" -lt "$MAX_WAIT" ]; do
  status="$(curl -s -o /dev/null -w "%{http_code}" "$PROD_URL/health" 2>/dev/null || echo "000")"
  if [ "$status" = "200" ]; then
    printf "  ✅ /health respondeu 200 após %ss\n" "$elapsed"
    break
  fi
  printf "  ⏳ %ss — status=%s, novo check em %ss\n" "$elapsed" "$status" "$POLL_INTERVAL"
  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))
done

if [ "$status" != "200" ]; then
  printf "\n❌ Rollback não validado: production não respondeu 200 em %ss (status=%s)\n" "$MAX_WAIT" "$status"
  exit 1
fi

printf "\n🔍 A correr smoke test pós-rollback...\n"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/smoke_test.py" "$PROD_URL"

printf "\n✅ Rollback concluído e validado.\n"
