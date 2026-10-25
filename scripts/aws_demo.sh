#!/usr/bin/env bash
# Brief AWS deployment of the CDK stack for a live demo - then tear it all down.
# Needs: AWS CLI v2 (logged in), Node.js (for npx aws-cdk), the infra Python deps, and
# build/ingest-deps-arm64.zip from `make lambda-layer`.
#
#   scripts/aws_demo.sh up       cdk bootstrap + deploy (demo DB access for this machine's IP only)
#   scripts/aws_demo.sh cert     create the site gateway's X.509 certificate and attach it
#   scripts/aws_demo.sh outputs  refresh .aws-demo/outputs.env
#   scripts/aws_demo.sh down     delete EVERYTHING: certificate, stack, CDK bootstrap, log groups
#
# State lives in .aws-demo/ (gitignored). See docs/adr/0009-aws-demo-deployment.md.
set -euo pipefail

cd "$(dirname "$0")/.."
REGION="${AWS_REGION:-eu-west-2}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""
ENV_NAME=dev
STACK="SmartCampus-$ENV_NAME"
CDK="npx -y aws-cdk@2.170.0"
# The CDK app (infra/app.py) needs aws-cdk-lib: prefer the project virtualenv's Python.
for venv_bin in .venv/Scripts .venv/bin; do
  [[ -d "$venv_bin" ]] && export PATH="$PWD/$venv_bin:$PATH" && break
done
STATE=.aws-demo
OUTPUTS="$STATE/outputs.env"
SECRETS="$STATE/secrets.env"
LAYER_ZIP="build/ingest-deps-arm64.zip"

log() { printf '\033[1m==> %s\033[0m\n' "$*"; }
# The Windows AWS CLI ends lines with \r\n; strip so captured values are clean.
aws_text() { aws "$@" --output text | tr -d '\r'; }
py() { "$(command -v python3 || command -v python)" "$@"; }

random_secret() { printf 'Aa1%s' "$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 29)"; }

up() {
  : "${BUDGET_EMAIL:?set BUDGET_EMAIL to the address for the zero-spend budget alert}"
  [[ -f "$LAYER_ZIP" ]] || { echo "Missing $LAYER_ZIP - run 'make lambda-layer' (in WSL/Linux) first"; exit 1; }
  mkdir -p "$STATE"
  local account ip
  account=$(aws_text sts get-caller-identity --query Account)
  ip="$(curl -fsS https://checkip.amazonaws.com | tr -d '\r\n')/32"
  log "Account $account, region $REGION, demo DB access for $ip only"

  log "CDK bootstrap (staging bucket for assets; removed again by 'down')"
  (cd infra && $CDK bootstrap "aws://$account/$REGION" --require-approval never)

  log "Deploying $STACK"
  (cd infra && $CDK deploy "$STACK" --require-approval never \
      -c demo_db_access="$ip" -c layer_zip="../$LAYER_ZIP" -c lambda_reserved_concurrency=0 \
      -c budget_email="$BUDGET_EMAIL" -c alarm_email="$BUDGET_EMAIL" \
      --outputs-file "../$STATE/cdk-outputs.json")
  write_outputs
  write_secrets
}

write_outputs() {
  local iot_endpoint
  iot_endpoint=$(aws_text iot describe-endpoint --endpoint-type iot:Data-ATS --query endpointAddress)
  py - "$STACK" "$iot_endpoint" > "$OUTPUTS" <<'PY'
import json, sys
stack, iot = sys.argv[1], sys.argv[2]
o = json.load(open(".aws-demo/cdk-outputs.json"))[stack]
lines = {
    "AWSD_DB_HOST": o["ClusterEndpoint"], "AWSD_ADMIN_SECRET_ARN": o["AdminSecretArn"],
    "AWSD_INGEST_SECRET_ARN": o["IngestSecretArn"], "AWSD_RAW_BUCKET": o["RawBucketName"],
    "AWSD_FUNCTION": o["IngestFunctionName"], "AWSD_GATEWAY_THING": o["GatewayThingName"],
    "AWSD_GATEWAY_POLICY": o["GatewayPolicyName"], "AWSD_IOT_ENDPOINT": iot,
}
sys.stdout.buffer.write("".join(f"{k}={v}\n" for k, v in lines.items()).encode())
PY
  log "Outputs written to $OUTPUTS"; cat "$OUTPUTS"
}

secret_password() {  # password field of an RDS-format Secrets Manager secret
  aws_text secretsmanager get-secret-value --secret-id "$1" --query SecretString \
    | py -c 'import json,sys; print(json.loads(sys.stdin.read())["password"])'
}

write_secrets() {
  # shellcheck disable=SC1090
  source "$OUTPUTS"
  umask 077
  {
    echo "AWSD_ADMIN_PASSWORD=$(secret_password "$AWSD_ADMIN_SECRET_ARN")"
    echo "AWSD_INGEST_DB_PASSWORD=$(secret_password "$AWSD_INGEST_SECRET_ARN")"
    # dbt and BI roles are only used from this machine during the demo
    echo "AWSD_TRANSFORM_DB_PASSWORD=$(random_secret)"
    echo "AWSD_BI_DB_PASSWORD=$(random_secret)"
  } > "$SECRETS"
  log "Database credentials written to $SECRETS"
}

cert() {
  # shellcheck disable=SC1090
  source "$OUTPUTS"
  if [[ -f "$STATE/gateway.cert.arn" ]]; then echo "certificate already exists"; return; fi
  log "Creating X.509 certificate for IoT Thing $AWSD_GATEWAY_THING"
  local arn
  arn=$(aws_text iot create-keys-and-certificate --set-as-active \
        --certificate-pem-outfile "$STATE/gateway.cert.pem" \
        --private-key-outfile "$STATE/gateway.private.key" --query certificateArn)
  echo "$arn" > "$STATE/gateway.cert.arn"
  aws iot attach-policy --policy-name "$AWSD_GATEWAY_POLICY" --target "$arn"
  aws iot attach-thing-principal --thing-name "$AWSD_GATEWAY_THING" --principal "$arn"
  log "Certificate attached: $arn"
}

delete_cert() {
  [[ -f "$STATE/gateway.cert.arn" ]] || return 0
  # shellcheck disable=SC1090
  source "$OUTPUTS"
  local arn id
  arn=$(cat "$STATE/gateway.cert.arn"); id="${arn##*/}"
  log "Deleting gateway certificate $id"
  aws iot detach-thing-principal --thing-name "$AWSD_GATEWAY_THING" --principal "$arn" || true
  aws iot detach-policy --policy-name "$AWSD_GATEWAY_POLICY" --target "$arn" || true
  aws iot update-certificate --certificate-id "$id" --new-status INACTIVE || true
  aws iot delete-certificate --certificate-id "$id" --force-delete || true
  rm -f "$STATE/gateway.cert.arn" "$STATE/gateway.cert.pem" "$STATE/gateway.private.key"
}

down() {
  if [[ "${FORCE:-}" != "1" ]]; then
    read -r -p "Delete the AWS demo ($STACK, CDK bootstrap, certificate, logs) in $REGION? Type 'delete': " answer
    [[ "$answer" == "delete" ]] || { echo "aborted"; exit 1; }
  fi
  local account
  account=$(aws_text sts get-caller-identity --query Account)
  delete_cert

  log "Deleting stack $STACK (empties the raw bucket via its auto-delete custom resource)"
  aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK"

  log "Deleting log groups left behind by Lambda / RDS"
  for prefix in "/aws/lambda/$STACK" "/aws/rds/cluster/smartcampus"; do
    for group in $(aws_text logs describe-log-groups --log-group-name-prefix "$prefix" --query 'logGroups[].logGroupName'); do
      aws logs delete-log-group --log-group-name "$group" && echo "  deleted $group"
    done
  done

  log "Removing the CDK bootstrap stack and its retained bucket / repository"
  local bucket="cdk-hnb659fds-assets-$account-$REGION"
  if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
    py - "$bucket" <<'PY'
import subprocess, sys, json
bucket = sys.argv[1]
while True:  # delete every object version (the bucket is versioned)
    out = subprocess.run(["aws", "s3api", "list-object-versions", "--bucket", bucket, "--max-items", "500",
                          "--output", "json"], capture_output=True, text=True, check=True).stdout
    data = json.loads(out or "{}")
    objs = [{"Key": v["Key"], "VersionId": v["VersionId"]}
            for v in data.get("Versions", []) + data.get("DeleteMarkers", [])]
    if not objs:
        break
    subprocess.run(["aws", "s3api", "delete-objects", "--bucket", bucket,
                    "--delete", json.dumps({"Objects": objs, "Quiet": True})], check=True)
PY
    aws s3api delete-bucket --bucket "$bucket" && echo "  deleted bucket $bucket"
  fi
  aws ecr delete-repository --repository-name "cdk-hnb659fds-container-assets-$account-$REGION" --force \
    >/dev/null 2>&1 && echo "  deleted ECR repository" || true
  aws cloudformation delete-stack --stack-name CDKToolkit
  aws cloudformation wait stack-delete-complete --stack-name CDKToolkit || true

  rm -f "$OUTPUTS" "$SECRETS" "$STATE/cdk-outputs.json"
  log "AWS demo removed. Check Billing > Bills over the next day to confirm \$0."
}

case "${1:-}" in
  up)      up ;;
  cert)    cert ;;
  outputs) write_outputs; write_secrets ;;
  down)    down ;;
  *)       sed -n '2,12p' "$0"; exit 1 ;;
esac
