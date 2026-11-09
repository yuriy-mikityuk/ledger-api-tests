#!/usr/bin/env bash
# Manual liveness check: account -> deposit -> signed webhook -> balance.
set -euo pipefail

BASE_URL="${LEDGER_BASE_URL:-http://localhost:8000}"
SECRET="${PROVIDER_WEBHOOK_SECRET:-dev-webhook-secret}"
API="$BASE_URL/api/v1"
AMOUNT="100.00"

json() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }

echo "1. Create account"
ACCOUNT=$(curl -sS --fail-with-body -X POST "$API/accounts/" \
  -H 'Content-Type: application/json' \
  -d "{\"email\": \"smoke-$(date +%s)-$RANDOM@example.com\"}")
TOKEN=$(echo "$ACCOUNT" | json "['token']")
WALLET_ID=$(echo "$ACCOUNT" | json "['wallets'][0]['id']")
CURRENCY=$(echo "$ACCOUNT" | json "['wallets'][0]['currency']")
echo "   wallet $WALLET_ID ($CURRENCY)"

echo "2. Create deposit"
DEPOSIT=$(curl -sS --fail-with-body -X POST "$API/deposits/" \
  -H "Authorization: Token $TOKEN" \
  -H "Idempotency-Key: smoke-$RANDOM-$RANDOM" \
  -H 'Content-Type: application/json' \
  -d "{\"wallet_id\": $WALLET_ID, \"amount\": \"$AMOUNT\"}")
REFERENCE=$(echo "$DEPOSIT" | json "['provider_reference']")
echo "   status $(echo "$DEPOSIT" | json "['status']"), reference $REFERENCE"

echo "3. Send signed webhook"
BODY="{\"event_id\": \"smoke-$RANDOM-$RANDOM\", \"event\": \"deposit.completed\", \"provider_reference\": \"$REFERENCE\", \"amount\": \"$AMOUNT\"}"
SIGNATURE=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $NF}')
curl -sS --fail-with-body -X POST "$API/webhooks/provider/" \
  -H "X-Provider-Signature: sha256=$SIGNATURE" \
  -H 'Content-Type: application/json' \
  --data-binary "$BODY"
echo

echo "4. Check balance"
BALANCE=$(curl -sS --fail-with-body "$API/wallets/" -H "Authorization: Token $TOKEN" \
  | json "[0]['balance']")
echo "   balance $BALANCE $CURRENCY"

if [ "$BALANCE" != "$AMOUNT" ]; then
  echo "FAIL: expected balance $AMOUNT, got $BALANCE" >&2
  exit 1
fi
echo "OK"
