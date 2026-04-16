#!/usr/bin/env bash
# Seed the local Docker Postgres with the Prisma schema for integration testing.
#
# Usage: ./scripts/seed-test-db.sh
#
# Prerequisites:
#   - Docker postgres container running: `make db-up`
#   - vendor/app.rightofaccess submodule initialized: `git submodule update --init`

set -euo pipefail

DB_URL="${DATABASE_URL:-postgresql://roa:roa@localhost:5433/roa_lambdas_dev}"
SCHEMA_FILE="scripts/schema.sql"
SUBMODULE_SCHEMA="vendor/app.rightofaccess/prisma/schema.prisma"

echo "==> Seeding local Postgres at ${DB_URL}"

# Check that the submodule is present
if [[ ! -f "${SUBMODULE_SCHEMA}" ]]; then
  echo "ERROR: Prisma schema not found at ${SUBMODULE_SCHEMA}" >&2
  echo "Run: git submodule update --init --recursive" >&2
  exit 1
fi

# Wait for Postgres to be ready
echo "==> Waiting for Postgres to be ready..."
ready=0
for i in $(seq 1 10); do
  if psql "${DB_URL}" -c '\q' 2>/dev/null; then
    ready=1
    break
  fi
  echo "    Attempt ${i}/10..."
  sleep 2
done
if [[ "${ready}" -eq 0 ]]; then
  echo "ERROR: Postgres did not become ready after 10 attempts. Is Docker running?" >&2
  exit 1
fi

# Apply the schema SQL (generated from Prisma schema)
if [[ -f "${SCHEMA_FILE}" ]]; then
  echo "==> Applying schema from ${SCHEMA_FILE}..."
  psql "${DB_URL}" -f "${SCHEMA_FILE}"
else
  echo "ERROR: ${SCHEMA_FILE} not found." >&2
  echo "Generate it by running: npx prisma migrate diff --from-empty --to-schema-datamodel ${SUBMODULE_SCHEMA} --script" >&2
  echo "Then save the output to ${SCHEMA_FILE} and commit it." >&2
  exit 1
fi

echo "==> Database seeded successfully."
