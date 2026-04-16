#!/usr/bin/env bash
# Invoke a Lambda function locally using SAM with a fixture event.
#
# Usage:
#   ./scripts/invoke-local.sh <function_logical_id> <event_file>
#
# Examples:
#   ./scripts/invoke-local.sh FhirProcessorFunction events/fhir-processor-sqs.json
#   ./scripts/invoke-local.sh PayerHealthCheckFunction events/payer-health-check-scheduled.json
#
# Prerequisites:
#   - Docker running
#   - .env.local populated (copy from .env.example)
#   - sam build run at least once: `sam build -t infra/template.yaml`

set -euo pipefail

FUNCTION_ID="${1:-FhirProcessorFunction}"
EVENT_FILE="${2:-events/fhir-processor-sqs.json}"
ENV_VARS_FILE=".env.local.json"

if [[ ! -f "${EVENT_FILE}" ]]; then
  echo "ERROR: Event file not found: ${EVENT_FILE}" >&2
  exit 1
fi

# Convert .env.local to SAM env-vars JSON if it exists and the JSON is stale
if [[ -f ".env.local" ]]; then
  if [[ ! -f "${ENV_VARS_FILE}" ]] || [[ ".env.local" -nt "${ENV_VARS_FILE}" ]]; then
    echo "==> Converting .env.local → ${ENV_VARS_FILE}..."
    python3 - "${FUNCTION_ID}" <<'PYEOF'
import json, pathlib, sys

env_file = pathlib.Path(".env.local")
function_id = sys.argv[1] if len(sys.argv) > 1 else "FhirProcessorFunction"
env_vars = {}
for line in env_file.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        continue
    key, _, val = line.partition("=")
    env_vars[key.strip()] = val.strip()

# SAM env-vars format: {"FunctionLogicalId": {"VAR": "value"}}
output = {function_id: env_vars}
pathlib.Path(".env.local.json").write_text(json.dumps(output, indent=2) + "\n")
print("Wrote .env.local.json", file=sys.stderr)
PYEOF
  fi
fi

ENV_ARGS=()
if [[ -f "${ENV_VARS_FILE}" ]]; then
  ENV_ARGS=(--env-vars "${ENV_VARS_FILE}")
fi

echo "==> Invoking ${FUNCTION_ID} with event: ${EVENT_FILE}"
sam local invoke \
  "${FUNCTION_ID}" \
  --event "${EVENT_FILE}" \
  --template infra/template.yaml \
  "${ENV_ARGS[@]}"
