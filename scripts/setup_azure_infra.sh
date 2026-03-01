#!/usr/bin/env bash
set -euo pipefail

# DBDE AI — Azure Infrastructure Setup
# Cria Key Vault, migra segredos e cria alertas Azure Monitor.
# Requer: az login + permissões no subscription.

APP_NAME="${APP_NAME:-millennium-ai-assistant}"
RG="${RG:-rg-MS_Access_Chabot}"
LOCATION="${LOCATION:-swedencentral}"
VAULT_NAME="${VAULT_NAME:-dbde-ai-vault}"
ACTION_GROUP="${ACTION_GROUP:-dbde-ai-alerts}"
ALERT_EMAIL="${ALERT_EMAIL:-}"

printf "=== DBDE AI Azure Infrastructure Setup ===\n\n"

printf "1) Ensure Key Vault exists...\n"
if az keyvault show --name "$VAULT_NAME" --resource-group "$RG" >/dev/null 2>&1; then
  printf "   Key Vault already exists: %s\n" "$VAULT_NAME"
else
  az keyvault create \
    --name "$VAULT_NAME" \
    --resource-group "$RG" \
    --location "$LOCATION" \
    >/dev/null
fi

printf "2) Resolve caller object id...\n"
CALLER_OBJECT_ID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)
if [ -n "$CALLER_OBJECT_ID" ]; then
  az keyvault set-policy \
    --name "$VAULT_NAME" \
    --object-id "$CALLER_OBJECT_ID" \
    --secret-permissions get list set delete >/dev/null || true
fi

printf "3) Create/ensure action group...\n"
if [ -n "$ALERT_EMAIL" ]; then
  az monitor action-group create \
    --name "$ACTION_GROUP" \
    --resource-group "$RG" \
    --short-name DBDEAlerts \
    --action email dbdeai-email "$ALERT_EMAIL" >/dev/null || true
else
  az monitor action-group create \
    --name "$ACTION_GROUP" \
    --resource-group "$RG" \
    --short-name DBDEAlerts >/dev/null || true
fi

printf "4) Create metric alerts...\n"
APP_RESOURCE_ID=$(az webapp show --name "$APP_NAME" --resource-group "$RG" --query id -o tsv)
PLAN_RESOURCE_ID=$(az webapp show --name "$APP_NAME" --resource-group "$RG" --query appServicePlanId -o tsv)
ACTION_GROUP_ID=$(az monitor action-group show --name "$ACTION_GROUP" --resource-group "$RG" --query id -o tsv)

az monitor metrics alert create \
  --name "dbde-high-error-rate" \
  --resource-group "$RG" \
  --scopes "$APP_RESOURCE_ID" \
  --condition "avg Http5xx > 5" \
  --window-size 5m \
  --evaluation-frequency 1m \
  --severity 2 \
  --action "$ACTION_GROUP_ID" \
  --description "DBDE AI: HTTP 5xx error rate > 5 in 5 minutes" >/dev/null || true

az monitor metrics alert create \
  --name "dbde-high-latency" \
  --resource-group "$RG" \
  --scopes "$APP_RESOURCE_ID" \
  --condition "avg HttpResponseTime > 30" \
  --window-size 5m \
  --evaluation-frequency 1m \
  --severity 3 \
  --action "$ACTION_GROUP_ID" \
  --description "DBDE AI: Average response time > 30s" >/dev/null || true

az monitor metrics alert create \
  --name "dbde-health-failures" \
  --resource-group "$RG" \
  --scopes "$APP_RESOURCE_ID" \
  --condition "total HealthCheckStatus < 1" \
  --window-size 5m \
  --evaluation-frequency 1m \
  --severity 1 \
  --action "$ACTION_GROUP_ID" \
  --description "DBDE AI: Health check failing" >/dev/null || true

az monitor metrics alert create \
  --name "dbde-high-cpu" \
  --resource-group "$RG" \
  --scopes "$PLAN_RESOURCE_ID" \
  --condition "avg CpuPercentage > 80" \
  --window-size 10m \
  --evaluation-frequency 5m \
  --severity 3 \
  --action "$ACTION_GROUP_ID" \
  --description "DBDE AI: CPU > 80% for 10 minutes" >/dev/null || true

printf "\nSetup concluido.\n"
printf '%s\n' "- Key Vault: $VAULT_NAME"
printf '%s\n' "- Action Group: $ACTION_GROUP"
printf '%s\n' "- Alerts: dbde-high-error-rate, dbde-high-latency, dbde-health-failures, dbde-high-cpu"
