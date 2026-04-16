# ROA Lambdas — Shared Agent Context

**Read this document in full before writing a single line of code.**

This document is shared between two agents completing the `roa-lambdas` project.
Agent 1 builds the shared layer + fhir_processor.
Agent 2 builds the remaining handlers + infra hardening.
The execution order is fixed: Agent 1's output is a hard dependency for Agent 2.

---

## What This Project Is

RightOfAccess (ROA) is a HIPAA-covered provider discovery tool for SSDI law firms. It uses
patient-authorized insurance claims data (Explanation of Benefits / EOB) from federally mandated
payer FHIR APIs to generate a complete map of every medical provider a claimant has seen.

The `roa-lambdas` repo contains all Python Lambda functions that handle FHIR processing, token
management, and payer monitoring. It is a **separate repo** from the Next.js web app
(`app.rightofaccess`). They share the same RDS database, the same `ENCRYPTION_KEY`, and the same
SQS queue contract.

---

## AWS Infrastructure — Already Provisioned

Do not create any of these resources. They exist in `us-east-1`.

| Resource | Name / ID | Notes |
|---|---|---|
| VPC | `roa-vpc` | 4 subnets, IGW, NAT gateway, route tables |
| Private subnets | 2 private subnets | Lambda placement; exact IDs in samconfig.toml |
| Security groups | `roa-lambda-sg`, `roa-rds-sg`, `roa-bastion-sg` | Lambda → RDS allowed on 5432 |
| RDS PostgreSQL | `roa-vpc` (private) | 6 Prisma migrations applied; sslmode=require |
| KMS key | `roa/s3-key` | S3 encryption; RDS uses existing key |
| Secrets Manager | 5 secrets (see below) | Already populated |
| S3 | `roa-sam-artifacts-prod` | SAM artifact bucket |
| S3 | `roa-cold-storage-prod` | EOB cold storage; Glacier lifecycle configured |
| IAM | Lambda execution role | SAM-managed via `infra/template.yaml` |
| BAA | AWS BAA signed | All PHI must stay within AWS |

**Secrets Manager secret names** (use these exact strings in env var references):
```
roa/database-url          → full Postgres URL with ?sslmode=require
roa/encryption-key        → 32-byte hex AES key
roa/bb-client-id          → CMS Blue Button OAuth client ID
roa/bb-client-secret      → CMS Blue Button OAuth client secret
roa/slack-webhook-url     → Slack incoming webhook for alerts
```

---

## Repo Structure — Current State

```
roa-lambdas/
├── conftest.py                    ✅ shared pytest fixtures
├── Makefile                       ✅ test, lint, deploy targets
├── samconfig.toml                 ✅ dev/staging/prod configs (VPC placeholders need filling)
├── pyproject.toml                 ✅
├── requirements.txt / -dev.txt    ✅
│
├── shared/                        LAYER — imported by all Lambdas
│   ├── __init__.py                ✅
│   ├── encryption.py              ✅ AES-256-GCM, matches lib/encryption.ts
│   ├── db.py                      ✅ all SQL helpers (see interface contract below)
│   ├── secrets.py                 ❌ MISSING — Agent 1 writes this
│   ├── logging.py                 ❌ MISSING — Agent 1 writes this
│   ├── payer_registry.py          ❌ MISSING — Agent 1 writes this
│   ├── audit.py                   ❌ MISSING — Agent 1 writes this
│   └── tests/
│       ├── conftest.py            ✅
│       ├── test_encryption.py     ✅
│       ├── test_db.py             ✅
│       ├── test_payer_registry.py ❌ MISSING — Agent 1 writes this
│       └── fixtures/
│           ├── ts_encrypted.bin   ✅ cross-language fixture
│           └── ts_encrypted_meta.json ✅
│
├── functions/
│   ├── fhir_processor/            ❌ handler + sub-modules MISSING — Agent 1 writes
│   │   ├── __init__.py            ✅
│   │   ├── handler.py             ❌
│   │   ├── fhir_client.py         ❌
│   │   ├── eob_parser.py          ❌
│   │   ├── nppes_resolver.py      ❌
│   │   ├── requirements.txt       ❌
│   │   └── tests/                 ❌ (test_handler, test_fhir_client, test_eob_parser,
│   │                                   test_nppes_resolver, fixtures/)
│   │
│   ├── payer_health_check/        ❌ handler MISSING — Agent 2 writes
│   │   ├── __init__.py            ✅
│   │   ├── handler.py             ❌
│   │   ├── requirements.txt       ❌
│   │   └── tests/test_handler.py  ❌
│   │
│   ├── token_refresh/             ❌ handler MISSING — Agent 2 writes
│   │   ├── __init__.py            ✅
│   │   ├── handler.py             ❌
│   │   ├── requirements.txt       ❌
│   │   └── tests/test_handler.py  ❌
│   │
│   ├── dlq_alerter/               ❌ handler MISSING — Agent 2 writes
│   │   ├── __init__.py            ✅
│   │   ├── handler.py             ❌
│   │   ├── requirements.txt       ❌
│   │   └── tests/test_handler.py  ❌
│   │
│   └── cold_storage_mover/        ❌ skeleton MISSING — Agent 2 writes (Phase 2)
│       ├── __init__.py            ✅
│       ├── handler.py             ❌
│       └── requirements.txt       ❌
│
├── infra/                         ✅ ALL SAM TEMPLATES COMPLETE
│   ├── template.yaml              ✅ root nested stack
│   ├── shared-layer.yaml          ✅
│   ├── fhir-processor.yaml        ✅
│   ├── payer-health-check.yaml    ✅
│   ├── token-refresh.yaml         ✅
│   ├── dlq-alerter.yaml           ✅
│   ├── cold-storage-mover.yaml    ✅
│   └── parameters/
│       ├── dev.json               ✅ (VPC placeholders need filling by human)
│       ├── staging.json           ✅
│       └── prod.json              ✅
│
├── scripts/                       ✅ ALL SCRIPTS COMPLETE
│   ├── build-layer.sh, invoke-local.sh, seed-test-db.sh
│   ├── smoke-test.sh, e2e-dev.sh
│   ├── check-schema-drift.py
│   └── generate-cross-lang-fixture.ts
│
└── .github/workflows/             ✅ ALL CI/CD COMPLETE
    ├── test.yml
    ├── deploy-dev.yml
    ├── deploy-staging.yml
    └── deploy-prod.yml
```

---

## Execution Order (Fixed)

```
Track A: shared layer          ← Agent 1 does first; everything imports from this
    ↓
Track B1: fhir_processor       ← Agent 1 does second; most complex Lambda
    ↓
Track B2: remaining handlers   ← Agent 2 does (after Agent 1's shared layer exists)
Track C: infra hardening       ← Agent 2 does in parallel with Track B2
    ↓
sam build + sam deploy         ← Human fills samconfig.toml placeholders, then deploys
    ↓
Amplify (Step 9)               ← Reads SQS queue URL from SSM Parameter Store
    ↓
CloudTrail                     ← Human sets up (no code dependency)
```

---

## Interface Contracts — Shared Layer

These are the exact function signatures both agents must use. Agent 1 implements them.
Agent 2 imports and calls them. Do not diverge from these signatures.

### `shared/secrets.py`
```python
def get_secret(name: str) -> str
    """Fetch from Secrets Manager; cached per Lambda container lifetime."""

def get_db_url() -> str
    """Convenience: get_secret('roa/database-url')"""

def get_encryption_key() -> str
    """Convenience: get_secret('roa/encryption-key')"""
```
- Reads secret name from the env var `DATABASE_URL_SECRET_NAME`, `ENCRYPTION_KEY_SECRET_NAME`, etc.
- Falls back to direct env var value when running locally (for tests without Secrets Manager)
- Cached with a module-level dict — one Secrets Manager call per cold start per secret

### `shared/logging.py`
```python
def get_logger(function_name: str) -> logging.Logger
    """Return a structured JSON logger. Call once at module level."""

def redact(data: dict, denylist: set[str] | None = None) -> dict
    """Replace PHI keys with '[REDACTED]'. Safe to call on any log metadata dict."""
```
- Every log line is JSON on one line (CloudWatch parses it)
- Default denylist: `{"access_token", "refresh_token", "claimant_name", "dob", "ssn",
  "phone", "email", "raw_json", "pkce_verifier"}`
- Required fields in every record: `timestamp`, `level`, `function_name`, `request_id`, `message`

### `shared/payer_registry.py`
```python
@dataclass(frozen=True)
class PayerConfig:
    slug: str
    name: str
    authorization_url: str
    token_url: str
    fhir_base_url: str
    scopes: list[str]
    use_pkce: bool

def get_payer_config(slug: str) -> PayerConfig
    """Raises KeyError if slug not found."""

def list_payers() -> list[str]
    """Returns all registered payer slugs."""
```
- Initial registry has one payer: `cms-blue-button`
- CMS Blue Button URLs use `BB_BASE_URL` env var (sandbox vs prod): 
  - authorization: `{BB_BASE_URL}/v2/o/authorize`
  - token: `{BB_BASE_URL}/v2/o/token`
  - FHIR: `{BB_BASE_URL}/v2/fhir`
- Scopes: `["patient/ExplanationOfBenefit.read", "patient/Patient.read", "patient/Coverage.read", "profile"]`
- PKCE required: `True`

### `shared/audit.py`
```python
def insert_audit_log(
    conn,
    action: str,
    resource_type: str,
    resource_id: str,
    firm_id: str | None = None,
    metadata: dict | None = None,
) -> None
    """Thin wrapper around db.insert_audit_log. Never logs PHI in metadata."""
```
- Valid `action` values: `"VIEW"`, `"CREATE"`, `"UPDATE"`, `"DELETE"`, `"EXPORT"`,
  `"EOB_PULL"`, `"TOKEN_REFRESH"`, `"HEALTH_CHECK"`

### `shared/db.py` — Already Written
Full helper list (do not re-implement, just import):
```python
get_connection()                    # contextmanager → psycopg2 conn
get_payer_token(conn, case_id, payer_slug)
update_payer_token(conn, token_id, access_token_enc, refresh_token_enc, expires_at)
list_expiring_tokens(conn, window_minutes)
get_case(conn, case_id)
update_case_status(conn, case_id, status)
upsert_eob_raw(conn, case_id, fhir_resource_id, raw_json_enc)
upsert_provider(conn, case_id, npi, name, specialty, address, phone) -> uuid
insert_encounter(conn, case_id, provider_id, date_of_service, dx_codes, cpt_codes, facility_name)
insert_prescription(conn, case_id, provider_id, drug_name, dosage, fill_date, pharmacy_name, pharmacy_npi)
get_nppes_cache(conn, npi)
upsert_nppes_cache(conn, npi, data_json)
update_payer_health(conn, payer_slug, status, response_time_ms, failures_delta)
insert_audit_log(conn, action, resource_type, resource_id, firm_id, metadata)
```

### `shared/encryption.py` — Already Written
```python
def encrypt(plaintext: str) -> bytes   # returns IV(12) || authTag(16) || ciphertext
def decrypt(data: bytes) -> str
```
Key loaded from `ENCRYPTION_KEY` env var (64 hex chars = 32 bytes).

---

## SQS Message Contract (Fixed — do not change)

Main queue (`eob-processing-{env}`) and DLQ (`eob-processing-dlq-{env}`) both carry:
```json
{ "caseId": "uuid-v4", "payerSlug": "cms-blue-button" }
```
This matches `app.rightofaccess/lib/sqs.ts`. Any change requires a matching PR in the web repo.

---

## Case Status Flow

```
PENDING_AUTH → AUTHORIZED → PROCESSING → COMPLETE
                                       ↘ ERROR
```
- Next.js OAuth callback sets `PROCESSING` before enqueuing the SQS message
- `fhir_processor` sets `COMPLETE` on success, `ERROR` on unrecoverable failure
- `dlq_alerter` sets `ERROR` when the message lands in the DLQ after 3 retries

---

## HIPAA Rules — Non-Negotiable

1. **Never log PHI.** PHI = claimant name, DOB, SSN, phone, email, OAuth tokens, raw FHIR JSON,
   diagnosis codes, any field linking a person to their health data. Use `shared.logging.redact()`.
2. **Encrypt before storing.** Any BYTEA column in the DB holds encrypted bytes from
   `shared.encryption.encrypt()`. Decrypt only in memory, immediately before use, then discard.
3. **Tokens never in logs or error messages.** Not even truncated.
4. **Audit every PHI access.** Every EOB pull, token refresh, and health check gets one audit row.
5. **`sslmode=require`** in every DB connection string. Already enforced by `db.get_connection()`.

---

## Testing Conventions

- `conftest.py` at repo root provides: `encryption_key_env` (autouse), `db_url`, `sample_case_id`,
  `sample_payer_slug` fixtures
- Use `pytest-mock` / `unittest.mock` for HTTP calls and AWS SDK calls
- Use `responses` library for mocking `requests` HTTP
- Integration tests that need real Postgres: use the `db_url` fixture and mark with
  `@pytest.mark.integration` — these run in CI (Postgres service container) but skip locally
  when `DATABASE_URL` is not set
- Never use `moto` for Secrets Manager in unit tests — mock `shared.secrets.get_secret` directly

---

## Vendor Submodule

`vendor/app.rightofaccess/` is a git submodule pointing to the web app repo.
- Prisma schema: `vendor/app.rightofaccess/prisma/schema.prisma`
- Payer registry (TS): `vendor/app.rightofaccess/lib/payers/registry.ts`
- Run `git submodule update --init --recursive` before reading these files

---

## What Happens After Code Is Complete

1. Human fills in VPC/subnet/SG IDs in `samconfig.toml` and `infra/parameters/*.json`
2. `make build` → `sam build --config-env dev --cached --parallel`
3. `sam deploy --config-env dev --no-confirm-changeset`
4. SAM writes queue URL to SSM Parameter Store at `/roa/dev/sqs/eob-processing-queue-url`
5. Amplify deploy reads that SSM path at runtime — no hardcoded queue URL in Amplify
6. CloudTrail enabled by human (no code dependency)
7. Track C (infra hardening) applied as a follow-up SAM deploy
