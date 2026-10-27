#!/usr/bin/env bash
# Mask secrets in a stream so command output can be shown or logged safely:
#   some-command 2>&1 | scripts/redact.sh
# Masks AWS access key ids, long base64/hex runs (secret keys, session tokens),
# KEY=value pairs that look like secrets, and 12-digit AWS account ids.
exec sed -E \
  -e 's/\b(AKIA|ASIA)[A-Z0-9]{16}\b/<aws-key-id>/g' \
  -e 's/[A-Za-z0-9+\/=]{40,}/<redacted>/g' \
  -e 's/((PASSWORD|SECRET|TOKEN|API_KEY)[A-Z_]*=)[^[:space:]]+/\1<redacted>/g' \
  -e 's/("(SecretAccessKey|SessionToken|AccessKeyId|password)"[[:space:]]*:[[:space:]]*")[^"]*/\1<redacted>/g' \
  -e 's/\b[0-9]{12}\b/<account>/g'
