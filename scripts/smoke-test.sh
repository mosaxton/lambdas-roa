#!/usr/bin/env bash
# Post-deploy smoke test: invoke payer_health_check with a synthetic event
# and verify it returns a non-error response.
#
# Usage: ./scripts/smoke-test.sh <environment>
# Example: ./scripts/smoke-test.sh dev
#
# Called by deploy-dev.yml after `sam deploy`.

set -euo pipefail

ENVIRONMENT="${1:-dev}"
FUNCTION_NAME="roa-payer-health-check-${ENVIRONMENT}"
REGION="${AWS_REGION:-us-east-1}"

echo "==> Smoke testing ${FUNCTION_NAME} in ${ENVIRONMENT}..."

# Create a minimal EventBridge scheduled event payload
PAYLOAD=$(python3 -c "
import json, datetime
print(json.dumps({
    'version': '0',
    'id': 'smoke-test',
    'detail-type': 'Scheduled Event',
    'source': 'aws.events',
    'account': '000000000000',
    'time': datetime.datetime.utcnow().isoformat() + 'Z',
    'region': '${REGION}',
    'resources': [],
    'detail': {}
}))
")

# Invoke the function synchronously
RESPONSE=$(aws lambda invoke \
  --function-name "${FUNCTION_NAME}" \
  --region "${REGION}" \
  --payload "${PAYLOAD}" \
  --cli-binary-format raw-in-base64-out \
  /tmp/smoke-test-response.json \
  --query 'StatusCode' \
  --output text)

echo "==> StatusCode: ${RESPONSE}"

if [[ "${RESPONSE}" != "200" ]]; then
  echo "ERROR: Smoke test failed — Lambda returned StatusCode ${RESPONSE}" >&2
  cat /tmp/smoke-test-response.json >&2
  exit 1
fi

# Check for function-level errors in the response
if grep -q '"errorMessage"' /tmp/smoke-test-response.json 2>/dev/null; then
  echo "ERROR: Smoke test failed — Lambda returned an error response:" >&2
  cat /tmp/smoke-test-response.json >&2
  exit 1
fi

echo "==> Smoke test passed for ${FUNCTION_NAME}."
