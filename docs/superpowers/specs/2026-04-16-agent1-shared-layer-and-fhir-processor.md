# Agent 1 Brief — Shared Layer + FHIR Processor

**Read `2026-04-16-roa-lambdas-shared-context.md` first. This document adds your specific tasks.**

You are Agent 1. You own:
- **Track A:** `shared/secrets.py`, `shared/logging.py`, `shared/payer_registry.py`, `shared/audit.py` + tests
- **Track B1:** `functions/fhir_processor/` — all four sub-modules + full test suite + EOB fixtures

Agent 2 is working in parallel on the remaining Lambda handlers and infra hardening. They will
import from the shared layer you write. Your interface signatures are locked in the shared context
doc — do not deviate from them.

**Build Track A before Track B1.** Every handler, including your own, imports from `shared/`.

---

## Track A — Shared Layer

### A1. `shared/secrets.py`

**Purpose:** One-call-per-cold-start Secrets Manager loader. Lambdas read secrets by name;
the cache means a warm invocation never hits Secrets Manager again.

**Implementation notes:**
- Module-level `_cache: dict[str, str] = {}` — persists across warm invocations
- `boto3.client("secretsmanager")` initialized lazily at module level
- Local fallback: if `boto3` raises `NoCredentialsError` or `EndpointResolutionError`, fall back
  to `os.environ.get(name, "")` so unit tests work without AWS credentials
- `get_db_url()` reads the secret whose name comes from `os.environ["DATABASE_URL_SECRET_NAME"]`,
  not the literal string `roa/database-url` — the secret name is injected by SAM as an env var
- Same pattern for `get_encryption_key()` using `ENCRYPTION_KEY_SECRET_NAME`

**Tests** (`shared/tests/test_secrets.py`):
- Mock `boto3` client; verify cache: second call does NOT hit Secrets Manager
- Verify local env var fallback when boto3 raises

---

### A2. `shared/logging.py`

**Purpose:** Structured JSON logger with PHI denylist. Every Lambda uses this; never `print()`.

**Implementation notes:**
- Single `logging.Logger` using `logging.StreamHandler(sys.stdout)` with a custom JSON formatter
- Formatter produces: `{"timestamp": ISO8601, "level": "INFO", "function_name": ...,
  "request_id": ..., "message": ..., ...extra_fields}`
- `request_id` comes from `context.aws_request_id` when available; defaults to `"local"`
- `redact(data, denylist)` does a shallow key check — replace value with `"[REDACTED]"` for
  any key in the denylist (case-insensitive match)
- Default denylist (always applied, cannot be removed by caller):
  `{"access_token", "refresh_token", "claimant_name", "dob", "ssn", "phone", "email",
    "raw_json", "pkce_verifier", "client_secret", "encryption_key"}`

**Tests** (`shared/tests/test_logging.py`):
- Verify output is valid JSON
- Verify denylist keys are replaced with `[REDACTED]`
- Verify caller-supplied extra keys in denylist are also redacted
- Verify clean keys pass through unchanged

---

### A3. `shared/payer_registry.py`

**Purpose:** Python mirror of `vendor/app.rightofaccess/lib/payers/registry.ts`.
One source of truth for FHIR URLs, OAuth endpoints, and scopes per payer.

**Implementation notes:**
- `BB_BASE_URL` env var drives the CMS Blue Button URLs (sandbox in dev/staging, prod in prod)
  Default: `https://sandbox.bluebutton.cms.gov`
- `get_payer_config(slug)` raises `KeyError` with message `f"Unknown payer slug: {slug!r}"` — 
  this is what the CloudWatch `UnknownPayerSlug` alarm fires on (log the error before raising)
- Build the PAYERS dict at import time using `os.environ.get("BB_BASE_URL", default)`

**Initial registry — one payer for MVP:**
```python
PayerConfig(
    slug="cms-blue-button",
    name="CMS Medicare (Blue Button)",
    authorization_url=f"{bb_base_url}/v2/o/authorize",
    token_url=f"{bb_base_url}/v2/o/token",
    fhir_base_url=f"{bb_base_url}/v2/fhir",
    scopes=["patient/ExplanationOfBenefit.read", "patient/Patient.read",
            "patient/Coverage.read", "profile"],
    use_pkce=True,
)
```

**Tests** (`shared/tests/test_payer_registry.py`):
- `get_payer_config("cms-blue-button")` returns correct URLs
- `get_payer_config("unknown-slug")` raises `KeyError`
- `list_payers()` returns `["cms-blue-button"]`
- URLs use `BB_BASE_URL` env var when set (monkeypatch)

---

### A4. `shared/audit.py`

**Purpose:** Thin convenience wrapper so handler code writes one clean line instead of three.

**Implementation notes:**
- Just calls `shared.db.insert_audit_log(conn, ...)` with the same signature
- Validates `action` is in the allowed set; raises `ValueError` on unknown action
- Calls `shared.logging.redact(metadata or {})` before passing to `db.insert_audit_log`
  so PHI cannot leak into the audit metadata even by mistake

---

## Track B1 — `functions/fhir_processor/`

This is the most complex Lambda. Read section 4.1 of the plan doc carefully. It pulls EOBs from
the payer FHIR API, parses them, resolves NPIs, and writes normalized rows to RDS.

### `functions/fhir_processor/requirements.txt`
```
requests
```
(All other deps come from the shared layer.)

### B1a. `functions/fhir_processor/eob_parser.py`

**Write this first** — it's a pure function with no I/O, easiest to TDD.

**Purpose:** `parse_eob(eob_resource: dict) -> dict` — converts one FHIR ExplanationOfBenefit
resource into a normalized dict the handler can write to the DB.

**Output shape:**
```python
{
    "claim_type": str,          # "CARRIER" | "INPATIENT" | "OUTPATIENT" | "SNF" |
                                #  "HOSPICE" | "HHA" | "DME" | "PDE"
    "providers": [              # list of providers referenced in this EOB
        {"npi": str, "role": str}   # role: "billing" | "performing" | "prescribing" | "facility"
    ],
    "encounters": [
        {
            "provider_npi": str,
            "date_of_service": str,     # ISO date string
            "dx_codes": list[str],      # ICD-10
            "cpt_codes": list[str],     # HCPCS/CPT
            "facility_name": str | None,
        }
    ],
    "prescriptions": [
        {
            "provider_npi": str | None,
            "drug_name": str,
            "dosage": str | None,
            "fill_date": str,           # ISO date string
            "pharmacy_name": str | None,
            "pharmacy_npi": str | None,
        }
    ],
}
```

**FHIR coding systems (hardcoded constants):**
```python
NPI_SYSTEM          = "http://hl7.org/fhir/sid/us-npi"
EOB_TYPE_SYSTEM     = "https://bluebutton.cms.gov/resources/codesystem/eob-type"
ICD10_SYSTEM        = "http://hl7.org/fhir/sid/icd-10-cm"
HCPCS_SYSTEM        = "https://bluebutton.cms.gov/resources/codesystem/hcpcs"
NDC_SYSTEM          = "http://hl7.org/fhir/sid/ndc"
CARE_TEAM_ROLE_SYS  = "http://hl7.org/fhir/us/carin-bb/CodeSystem/C4BBClaimCareTeamRole"
```

**Parsing rules:**
- Extract claim type from `resource["type"]["coding"]` where `system == EOB_TYPE_SYSTEM`
- Extract NPIs from `resource["careTeam"]` entries where identifier system == `NPI_SYSTEM`
- For PDE (Part D drug) EOBs: extract prescribing NPI from `careTeam`, drug from
  `resource["item"][0]["productOrService"]["coding"]` where system == `NDC_SYSTEM`
- For INPATIENT/OUTPATIENT: extract facility name from `resource["facility"]["display"]` if present
- Missing NPI system → skip that care team entry silently (no crash)
- Empty `careTeam` → return empty providers list (handler will use billing provider fallback)
- Malformed dates → log warning, use `None`

**Tests** (`functions/fhir_processor/tests/test_eob_parser.py`) — minimum coverage:
- CARRIER EOB → billing + performing provider extracted, dx codes, cpt codes, service date
- PDE EOB → prescribing provider NPI, NDC drug code, fill date
- INPATIENT EOB → facility name, attending/operating providers
- Empty `careTeam` → empty providers list, no exception
- Missing NPI system in identifier → provider entry skipped
- Load real fixture files from `tests/fixtures/` for each claim type

**Fixtures** (`functions/fhir_processor/tests/fixtures/`):
Create minimal but realistic JSON fixtures for: `carrier_eob.json`, `pde_eob.json`,
`inpatient_eob.json`, `bundle_page.json` (a Bundle with a `next` link).
Base them on the CMS Blue Button sandbox data shape.

---

### B1b. `functions/fhir_processor/fhir_client.py`

**Purpose:** `fetch_all_eobs(fhir_base: str, patient_id: str, access_token: str, page_size: int = 50)`
— async-style generator that yields one FHIR ExplanationOfBenefit resource at a time,
following Bundle pagination and applying retry/backoff.

**Implementation notes:**
- Uses `requests.Session` with `Authorization: Bearer {access_token}` and
  `Accept: application/fhir+json` headers
- First request: `GET {fhir_base}/ExplanationOfBenefit?patient={patient_id}&_count={page_size}`
- Follow `Bundle.link` where `relation == "next"` until no next link
- Backoff on `429` / `503`: read `Retry-After` header if present (seconds to sleep),
  else exponential backoff: 2s, 4s, 8s (max 3 retries per page)
- Raise `requests.HTTPError` after max retries
- Yield individual resources from `Bundle.entry[*].resource`
- Never log the access token

**Tests** (`functions/fhir_processor/tests/test_fhir_client.py`):
- Use `responses` library to mock HTTP
- Pagination: two-page Bundle is fully consumed (all resources yielded)
- `429` with `Retry-After: 2` → sleeps 2s, retries, succeeds
- `503` → exponential backoff, eventually raises after max retries
- Empty bundle (`entry: []`) → yields nothing, no crash

---

### B1c. `functions/fhir_processor/nppes_resolver.py`

**Purpose:** `resolve_npi(npi: str, conn) -> dict | None`
— checks RDS cache first; if stale or missing, calls NPPES API.

**NPPES API:**
- Base URL from `os.environ.get("NPPES_API_URL", "https://npiregistry.cms.hhs.gov/api/")`
- `GET {NPPES_API_URL}?number={npi}&version=2.1`
- On 404 or empty results: return `None`
- Cache TTL from `int(os.environ.get("NPPES_CACHE_TTL_HOURS", "168"))` hours

**Output shape (when NPI found):**
```python
{
    "name": str,            # "Last, First" for individuals; org name for organizations
    "specialty": str,       # first taxonomy description, or ""
    "address": str,         # "123 Main St, City, ST 12345"
    "phone": str,           # formatted phone or ""
}
```

**Implementation flow:**
1. `get_nppes_cache(conn, npi)` → if row exists and `updated_at > now() - TTL` → return parsed data
2. Call NPPES API → parse response
3. `upsert_nppes_cache(conn, npi, json.dumps(raw_response))` → store raw response
4. Return parsed dict

**Tests** (`functions/fhir_processor/tests/test_nppes_resolver.py`):
- Cache hit within TTL → no HTTP call made
- Cache miss → HTTP call made, result cached
- Cache stale → HTTP call made, cache refreshed
- NPPES 404 → returns `None`
- Organization vs individual name parsing (org: `basic.organization_name`,
  individual: `basic.last_name + ", " + basic.first_name`)

---

### B1d. `functions/fhir_processor/handler.py`

**Purpose:** Lambda entrypoint. Wires together all the above sub-modules following the
16-step flow from section 4.1 of the plan.

**Signature:**
```python
def handler(event: dict, context) -> dict:
    """SQS trigger. event["Records"] has exactly one record (BatchSize=1)."""
```

**Critical implementation details:**

1. Parse SQS body: `body = json.loads(event["Records"][0]["body"])`
2. Validate: UUID format on `case_id`, `payer_slug` in `payer_registry.list_payers()`
3. Load `payer_config = get_payer_config(payer_slug)`
4. **Open DB, fetch tokens, check freshness, decrypt** — then **close DB** before FHIR fetch
   (never hold a DB connection open during FHIR pagination — it's slow)
5. Token freshness: if `expires_at < datetime.utcnow()`, attempt refresh via payer token URL
   using `requests.post(payer_config.token_url, data={...}, auth=(client_id, client_secret))`
   If refresh fails: `update_case_status(ERROR)`, write audit log, re-raise
6. Decrypt access token + patient FHIR ID using `shared.encryption.decrypt()`
7. `fetch_all_eobs(...)` → for each EOB:
   - Re-open DB connection
   - `upsert_eob_raw(conn, case_id, eob["id"], encrypt(json.dumps(eob)))`
   - `parse_eob(eob)` → accumulate providers (deduped by NPI), encounters, prescriptions
8. Resolve NPIs → `upsert_provider(...)` → build `npi_to_provider_id` map
9. `insert_encounter(...)` for each encounter, resolving `provider_npi → provider_id`
10. `insert_prescription(...)` for each prescription
11. `update_case_status(conn, case_id, "COMPLETE")`
12. `insert_audit_log(conn, action="EOB_PULL", ..., metadata={eob_count, provider_count,
    encounter_count, prescription_count, duration_ms, payer_slug})` — NO PHI in metadata
13. Return `{"batchItemFailures": []}` on success
    On exception: log traceback, `update_case_status("ERROR")`, audit log with
    `metadata={"error_type": type(e).__name__, "error_message": str(e)[:200]}`, re-raise

**Error categories:**
- `ValueError` (bad input) → ERROR status, re-raise → DLQ after 3 SQS retries
- `psycopg2.OperationalError` → let propagate (transient, SQS retries)
- HTTP 4xx from payer (not 401/403) → ERROR status, re-raise
- HTTP 401/403 → attempt one token refresh, then ERROR if still failing
- HTTP 429/503 → already handled by `fhir_client` backoff; if exhausted, let propagate
- NPPES failures → non-fatal, insert placeholder provider: `{name: f"NPI {npi}", specialty: "",
  address: "", phone: ""}` and continue

**Environment variables the handler reads:**
```
DATABASE_URL_SECRET_NAME
ENCRYPTION_KEY_SECRET_NAME
BB_CLIENT_ID_SECRET_NAME
BB_CLIENT_SECRET_SECRET_NAME
BB_BASE_URL
NPPES_API_URL
NPPES_CACHE_TTL_HOURS
EOB_DLQ_URL          (for logging purposes only — not used directly)
ENVIRONMENT
```

**Tests** (`functions/fhir_processor/tests/test_handler.py`):
- Happy path: SQS event → mock FHIR (2 EOBs) → verify DB writes (eob_raw, providers,
  encounters, prescriptions), case status COMPLETE, audit log written
- Invalid UUID → ValueError raised, case not updated (case may not exist yet)
- Unknown payer slug → ValueError, case set to ERROR
- Expired token → refresh called, continues if refresh succeeds
- Expired token + refresh fails → case set to ERROR, re-raises
- FHIR 429 exhausted → propagates (SQS will retry)
- NPPES 404 → placeholder provider inserted, processing continues
- DB connection lost mid-processing → propagates (SQS will retry)

---

## What NOT to Do

- Do not write any `infra/` YAML — all SAM templates are already complete
- Do not write `functions/payer_health_check/`, `functions/token_refresh/`,
  `functions/dlq_alerter/`, or `functions/cold_storage_mover/` — those are Agent 2's
- Do not modify `shared/encryption.py` or `shared/db.py` — already written and tested
- Do not modify `conftest.py` at repo root — already correct
- Do not modify any `scripts/` or `.github/workflows/` files

---

## Definition of Done (Track A + B1)

- [ ] `make test` passes with zero failures (all new tests green)
- [ ] `make lint` passes (ruff + black + mypy) with no errors
- [ ] `python scripts/check-schema-drift.py` exits 0
- [ ] `shared/payer_registry.py` exists and `check-schema-drift.py` finds it (no silent pass)
- [ ] No PHI appears in any log output from the test suite
- [ ] All new files have type annotations (mypy --strict clean)
- [ ] Each `functions/fhir_processor/tests/fixtures/*.json` is a valid FHIR resource
- [ ] `git push` to `main` — CI green
