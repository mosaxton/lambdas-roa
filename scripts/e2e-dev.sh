#!/usr/bin/env bash
# End-to-end dev test: trigger the full FHIR processing pipeline for a real case.
#
# Prerequisites:
#   1. Next.js dev app running and pointed at dev RDS
#   2. Complete the Blue Button sandbox OAuth flow for a case
#   3. Note the case ID from the URL or DB
#
# Usage: ./scripts/e2e-dev.sh <case_id> [payer_slug]
# Example: ./scripts/e2e-dev.sh 12345678-1234-1234-1234-123456789012 cms-blue-button

set -euo pipefail

CASE_ID="${1:-}"
PAYER_SLUG="${2:-cms-blue-button}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
REGION="${AWS_REGION:-us-east-1}"
QUEUE_URL="${SQS_EOB_QUEUE_URL:-}"

if [[ -z "${CASE_ID}" ]]; then
  echo "Usage: $0 <case_id> [payer_slug]" >&2
  exit 1
fi

if [[ -z "${QUEUE_URL}" ]]; then
  echo "ERROR: SQS_EOB_QUEUE_URL is not set." >&2
  echo "  Export it from Parameter Store:" >&2
  echo "  export SQS_EOB_QUEUE_URL=\$(aws ssm get-parameter --name /roa/${ENVIRONMENT}/sqs/eob-processing-queue-url --query Parameter.Value --output text)" >&2
  exit 1
fi

echo "==> E2E test: sending case ${CASE_ID} (${PAYER_SLUG}) to SQS queue"
echo "    Queue: ${QUEUE_URL}"

BODY=$(python3 -c "import json,sys; print(json.dumps({'caseId': sys.argv[1], 'payerSlug': sys.argv[2]}))" "${CASE_ID}" "${PAYER_SLUG}")

aws sqs send-message \
  --queue-url "${QUEUE_URL}" \
  --message-body "${BODY}" \
  --region "${REGION}"

echo "==> Message sent. The fhir_processor Lambda will pick it up within seconds."
echo "    Monitor progress in CloudWatch Logs: /aws/lambda/roa-fhir-processor-${ENVIRONMENT}"
echo "    Check the UI at /cases/${CASE_ID} to verify the provider map populated."
