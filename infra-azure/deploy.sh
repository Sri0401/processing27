#!/usr/bin/env bash
# Deploy / tear down the Azure environment. Needs the Azure CLI (logged in) and openssl.
#
#   infra-azure/deploy.sh infra     resource group + data/secret/observability resources (no apps)
#   infra-azure/deploy.sh apps      + container apps, using images ghcr.io/$GHCR_OWNER/...:$IMAGE_TAG
#   infra-azure/deploy.sh outputs   refresh .azure/outputs.env from the last deployment
#   infra-azure/deploy.sh down      delete the resource group and purge the Key Vault
#
# State lives in .azure/ (gitignored): secrets.env holds generated passwords (chmod 600)
# and outputs.env holds hostnames/URLs used by the `make azure-*` data targets.
set -euo pipefail

cd "$(dirname "$0")/.."
RG="${AZ_RESOURCE_GROUP:-rg-smartcampus-dev}"
LOCATION="${AZ_LOCATION:-australiaeast}"
ENV_NAME="${AZ_ENV_NAME:-dev}"
GHCR_OWNER="${GHCR_OWNER:-sri0401}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse HEAD)}"
STATE=.azure
SECRETS="$STATE/secrets.env"
OUTPUTS="$STATE/outputs.env"
DEPLOYMENT=smartcampus

log() { printf '\033[1m==> %s\033[0m\n' "$*"; }
# The Windows build of the Azure CLI ends lines with \r\n; strip it so captured values are clean.
az_tsv() { az "$@" -o tsv | tr -d '\r'; }

random_secret() {  # 32 chars; the Aa1 prefix satisfies PostgreSQL complexity rules
  printf 'Aa1%s' "$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 29)"
}

ensure_secrets() {
  mkdir -p "$STATE"
  if [[ ! -s "$SECRETS" ]]; then
    log "Generating secrets in $SECRETS"
    umask 077
    {
      for name in PG_ADMIN_PASSWORD INGEST_DB_PASSWORD TRANSFORM_DB_PASSWORD BI_DB_PASSWORD \
                  INGEST_API_KEY GRAFANA_ADMIN_PASSWORD; do
        echo "AZ_${name}=$(random_secret)"
      done
    } > "$SECRETS"
  fi
  # shellcheck disable=SC1090
  source "$SECRETS"
}

deploy() {
  local deploy_apps="$1"
  ensure_secrets
  log "Resource group $RG ($LOCATION)"
  az group create -n "$RG" -l "$LOCATION" --tags project=smart-campus environment="$ENV_NAME" -o none

  local params="$STATE/params.json"
  # Expanded now (double quotes): the trap runs after this function's locals are gone.
  trap "rm -f '$params'" EXIT
  umask 077
  cat > "$params" <<JSON
{
  "envName": {"value": "$ENV_NAME"},
  "deployerObjectId": {"value": "$(az_tsv ad signed-in-user show --query id)"},
  "deployerIp": {"value": "$(curl -fsS https://api.ipify.org)"},
  "alertEmail": {"value": "${ALERT_EMAIL:-$(az_tsv account show --query user.name)}"},
  "deployApps": {"value": $deploy_apps},
  "platformImage": {"value": "ghcr.io/$GHCR_OWNER/smart-campus-platform:$IMAGE_TAG"},
  "grafanaImage": {"value": "ghcr.io/$GHCR_OWNER/smart-campus-grafana:$IMAGE_TAG"},
  "pgAdminPassword": {"value": "$AZ_PG_ADMIN_PASSWORD"},
  "ingestDbPassword": {"value": "$AZ_INGEST_DB_PASSWORD"},
  "transformDbPassword": {"value": "$AZ_TRANSFORM_DB_PASSWORD"},
  "biDbPassword": {"value": "$AZ_BI_DB_PASSWORD"},
  "ingestApiKey": {"value": "$AZ_INGEST_API_KEY"},
  "grafanaAdminPassword": {"value": "$AZ_GRAFANA_ADMIN_PASSWORD"}
}
JSON
  log "Deploying infra-azure/main.bicep (deployApps=$deploy_apps)"
  az deployment group create -g "$RG" -n "$DEPLOYMENT" -f infra-azure/main.bicep -p "@$params" -o none
  rm -f "$params"  # contains secrets: remove as soon as it has been used
  write_outputs

  # TimescaleDB is loaded via shared_preload_libraries, which only takes effect after a restart.
  if [[ ! -f "$STATE/.postgres-restarted" ]]; then
    log "Restarting PostgreSQL once to load TimescaleDB"
    az postgres flexible-server restart -g "$RG" -n "$AZ_PG_SERVER" -o none
    touch "$STATE/.postgres-restarted"
  fi
}

write_outputs() {
  local out
  out=$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query properties.outputs -o json)
  python_cmd=$(command -v python3 || command -v python)
  # Written as bytes with \n so the file is readable by make/bash on Linux and WSL alike.
  "$python_cmd" -c '
import json, sys
o = json.load(sys.stdin)
names = {"postgresHost": "AZ_DB_HOST", "postgresServerName": "AZ_PG_SERVER", "keyVaultName": "AZ_KEYVAULT",
         "storageBlobEndpoint": "AZ_BLOB_ENDPOINT", "ingestUrl": "AZ_INGEST_URL", "grafanaUrl": "AZ_GRAFANA_URL"}
lines = [env + "=" + str(o[key]["value"]) for key, env in names.items()]
sys.stdout.buffer.write(("\n".join(lines) + "\n").encode())
' <<<"$out" > "$OUTPUTS"
  # shellcheck disable=SC1090
  source "$OUTPUTS"
  log "Outputs written to $OUTPUTS"
  cat "$OUTPUTS"
}

down() {
  if [[ "${FORCE:-}" != "1" ]]; then
    read -r -p "Delete resource group '$RG' and ALL data in it? Type the name to confirm: " answer
    [[ "$answer" == "$RG" ]] || { echo "aborted"; exit 1; }
  fi
  local vault
  vault=$(az_tsv keyvault list -g "$RG" --query "[0].name" 2>/dev/null || true)
  log "Deleting resource group $RG"
  az group delete -n "$RG" --yes
  if [[ -n "$vault" ]]; then
    log "Purging soft-deleted Key Vault $vault"
    az keyvault purge --name "$vault" -o none || true
  fi
  rm -f "$OUTPUTS" "$STATE/.postgres-restarted"
  log "Done. Generated secrets are kept in $SECRETS (delete it to rotate on the next deploy)."
}

case "${1:-}" in
  infra)   deploy false ;;
  apps)    deploy true ;;
  outputs) write_outputs ;;
  down)    down ;;
  *)       sed -n '2,10p' "$0"; exit 1 ;;
esac
