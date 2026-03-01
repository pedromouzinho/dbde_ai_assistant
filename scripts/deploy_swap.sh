#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-millennium-ai-assistant}"
RG="${RG:-rg-MS_Access_Chabot}"
SLOT="${SLOT:-staging}"
BASE_URL="https://${APP_NAME}-${SLOT}.azurewebsites.net"

printf "=== DBDE AI Deploy: swap staging -> production ===\n\n"

printf "1. Running smoke test on staging...\n"
python3 scripts/smoke_test.py "$BASE_URL"

printf "\n2. Swapping staging -> production...\n"
az webapp deployment slot swap \
  --name "$APP_NAME" \
  --resource-group "$RG" \
  --slot "$SLOT" \
  --target-slot production

printf "\n3. Verifying production...\n"
sleep 5
python3 scripts/smoke_test.py "https://${APP_NAME}.azurewebsites.net"

printf "\nDeploy swap concluido com sucesso.\n"
