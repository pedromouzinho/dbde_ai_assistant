#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-millennium-ai-assistant}"
RG="${RG:-rg-MS_Access_Chabot}"
SLOT="${SLOT:-staging}"

printf "=== DBDE AI Rollback: swap back production -> staging ===\n\n"

az webapp deployment slot swap \
  --name "$APP_NAME" \
  --resource-group "$RG" \
  --slot "$SLOT" \
  --target-slot production

printf "Rollback concluido. Valida /health e /api/info em production.\n"
