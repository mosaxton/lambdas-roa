# Agent 2 Brief — Remaining Handlers + Infra Hardening

**Read `2026-04-16-roa-lambdas-shared-context.md` first. This document adds your specific tasks.**

You are Agent 2. You own:
- **Track B2:** `functions/payer_health_check/`, `functions/token_refresh/`,
  `functions/dlq_alerter/`, `functions/cold_storage_mover/` (skeleton only)
- **Track C:** Infra hardening in `infra/template.yaml` and function YAML templates

Agent 1 is building the `shared/` layer and `functions/fhir_processor/`. You depend on the
shared layer they write. The interface contracts are fully specified in the shared context doc —
import those modules exactly as documented. Do not write your own versions of anything in `shared/`.

**Important:** The shared layer (`shared/secrets.py`, `shared/logging.py`, `shared/payer_registry.py`,
`shared/audit.py`) may not be committed yet when you start. Write your handlers to import from
those modules using the exact signatures in the shared context doc. Your tests should mock those
imports. The integration will work once Agent 1 pushes their work.

---

## Track B2 — Lambda Handlers

All four handlers follow the same pattern:
1. Import from `shared` (logging, secrets, db, payer_registry, audit, encryption)
2. Get logger at module level: `logger = get_logger(os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "local"))`
3. Load secrets at module level (cold start) via `shared.secrets`
4. Open DB connection in handler body using `shared.db.get_connection()`
5. Write one audit log row per meaningful action

### `functions/*/requirements.txt`

All four handlers need only:
```
requests
```
(Everything else comes from the shared Lambda layer.)

---

### B2a. `functions/payer_health_check/handler.py`

**Trigger:** EventBridge, every 5 minutes.
**Purpose:** Ping each payer's FHIR `/metadata` endpoint. Record response time, track failures,
Slack alert after 3 consecutive failures.

**Flow:**
```python
def handler(event: dict, context) -> None:
    payers = list_payers()                          # from shared.payer_registry
    with get_connection() as conn:
        for slug in payers:
            config = get_payer_config(slug)
            url = f"{config.fhir_base_url}/metadata"
            start = time.monotonic()
            try:
                resp = requests.get(url, headers={"Accept": "application/fhir+json"}, timeout=10)
                elapsed_ms = int((time.monotonic() - start) * 1000)

                if resp.status_code == 200 and elapsed_ms <= 3000:
                    status, failures_delta = "HEALTHY", 0
                elif resp.status_code == 200:           # slow but alive
                    status, failures_delta = "DEGRADED", 0
                else:
                    status, failures_delta = "DOWN", 1
            except requests.Timeout:
                elapsed_ms = 10_000
                status, failures_delta = "DOWN", 1

            update_payer_health(conn, slug, status, elapsed_ms, failures_delta)

            # check consecutive_failures after update
            row = conn.execute("SELECT consecutive_failures FROM payer_health WHERE payer_slug=%s", (slug,)).fetchone()
            if row and row["consecutive_failures"] >= 3:
                _post_slack_alert(slug, status, elapsed_ms)

            insert_audit_log(conn, action="HEALTH_CHECK", resource_type="payer",
                             resource_id=slug, metadata={"status": status, "response_time_ms": elapsed_ms})
```

**Slack alert function:**
```python
def _post_slack_alert(slug: str, status: str, response_time_ms: int) -> None:
    webhook_url = get_secret(os.environ["SLACK_WEBHOOK_SECRET_NAME"])
    requests.post(webhook_url, json={
        "text": f":warning: Payer *{slug}* is {status} — "
                f"3 consecutive failures. Last response: {response_time_ms}ms."
    }, timeout=5)
```

**Environment variables:**
```
DATABASE_URL_SECRET_NAME
SLACK_WEBHOOK_SECRET_NAME
ENVIRONMENT
```

**Tests** (`functions/payer_health_check/tests/test_handler.py`):
- Healthy payer (HTTP 200, fast) → status HEALTHY, failures_delta=0, no Slack call
- Slow payer (HTTP 200, >3s) → status DEGRADED, no Slack call
- Down payer (HTTP 500) → status DOWN, failures_delta=1
- Third consecutive failure → Slack alert posted
- Timeout → status DOWN, failures_delta=1
- Audit log row written for each payer regardless of outcome
- Mock `shared.db`, `shared.payer_registry`, `shared.secrets`, `requests`

---

### B2b. `functions/token_refresh/handler.py`

**Trigger:** EventBridge, every 15 minutes.
**Purpose:** Proactively refresh OAuth access tokens expiring within the next 20 minutes.
Keeps `fhir_processor` from hitting a mid-pull expiry.

**Flow:**
```python
def handler(event: dict, context) -> None:
    with get_connection() as conn:
        tokens = list_expiring_tokens(conn, window_minutes=20)
        for token in tokens:
            config = get_payer_config(token["payer_slug"])
            client_id_secret = os.environ[f"BB_CLIENT_ID_SECRET_NAME"]   # extend for other payers
            client_id = get_secret(client_id_secret)
            client_secret = get_secret(os.environ["BB_CLIENT_SECRET_SECRET_NAME"])

            refresh_token_plain = decrypt(token["refresh_token_enc"])

            try:
                resp = requests.post(
                    config.token_url,
                    data={"grant_type": "refresh_token", "refresh_token": refresh_token_plain},
                    auth=(client_id, client_secret),
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                new_access_enc = encrypt(data["access_token"])
                new_refresh_enc = encrypt(data.get("refresh_token", refresh_token_plain))
                new_expires_at = datetime.utcnow() + timedelta(seconds=data["expires_in"])

                update_payer_token(conn, token["id"], new_access_enc, new_refresh_enc, new_expires_at)
                insert_audit_log(conn, action="TOKEN_REFRESH", resource_type="payer_token",
                                 resource_id=token["id"],
                                 metadata={"payer_slug": token["payer_slug"], "result": "refreshed"})

            except requests.HTTPError as e:
                if e.response.status_code in (400, 401):
                    # Refresh token revoked — do not touch the DB row
                    logger.warning("Refresh token revoked", extra={"token_id": token["id"],
                                   "payer_slug": token["payer_slug"]})
                    insert_audit_log(conn, action="TOKEN_REFRESH", resource_type="payer_token",
                                     resource_id=token["id"],
                                     metadata={"payer_slug": token["payer_slug"], "result": "revoked"})
                else:
                    logger.error("Token refresh network error — will retry next cron run",
                                 extra={"error": str(e)})
```

**Environment variables:**
```
DATABASE_URL_SECRET_NAME
ENCRYPTION_KEY_SECRET_NAME
BB_CLIENT_ID_SECRET_NAME
BB_CLIENT_SECRET_SECRET_NAME
ENVIRONMENT
```

**Tests** (`functions/token_refresh/tests/test_handler.py`):
- Token expiring within 20 min → refresh called → `update_payer_token` called with new encrypted values
- Token refresh HTTP 400 (revoked) → DB row untouched, audit log written with `result: "revoked"`
- Token refresh HTTP 400 (revoked) → CloudWatch metric `RevokedRefreshToken` emitted
  (the CloudWatch alarm on this metric is already configured in `infra/token-refresh.yaml`)
- Token refresh network error → logs error, moves on to next token
- Multiple expiring tokens → all processed in one invocation
- Mock `shared.db`, `shared.secrets`, `shared.encryption`, `requests`

---

### B2c. `functions/dlq_alerter/handler.py`

**Trigger:** SQS — the `eob-processing-dlq-{env}` queue, batch size 1.
**Purpose:** Mark case ERROR in RDS, write audit log, post Slack alert. Ensures the paralegal
sees `ERROR` in the UI instead of a stuck `PROCESSING`.

**Flow:**
```python
def handler(event: dict, context) -> None:
    record = event["Records"][0]
    body = json.loads(record["body"])
    case_id = body.get("caseId")
    payer_slug = body.get("payerSlug", "unknown")

    if not case_id:
        logger.error("DLQ message missing caseId", extra={"body_keys": list(body.keys())})
        return   # return success so the broken message is deleted and not re-queued forever

    with get_connection() as conn:
        case = get_case(conn, case_id)
        if not case:
            logger.warning("DLQ message for non-existent case", extra={"case_id": case_id})
            return

        update_case_status(conn, case_id, "ERROR")
        insert_audit_log(conn, action="EOB_PULL", resource_type="case",
                         resource_id=case_id, firm_id=case["firm_id"],
                         metadata={"result": "dlq", "payer_slug": payer_slug,
                                   "note": "max receive count exceeded"})

    _post_slack_alert(case_id, payer_slug)
```

**Slack alert function:**
```python
def _post_slack_alert(case_id: str, payer_slug: str) -> None:
    webhook_url = get_secret(os.environ["SLACK_WEBHOOK_SECRET_NAME"])
    requests.post(webhook_url, json={
        "text": f":red_circle: Case `{case_id}` failed FHIR processing "
                f"(payer: *{payer_slug}*) — sent to DLQ after 3 retries."
    }, timeout=5)
```

**Environment variables:**
```
DATABASE_URL_SECRET_NAME
SLACK_WEBHOOK_SECRET_NAME
ENVIRONMENT
```

**Tests** (`functions/dlq_alerter/tests/test_handler.py`):
- Normal DLQ message → case set to ERROR, audit log written, Slack posted
- Case not found in DB → returns without error (message deleted from DLQ)
- Missing `caseId` in body → returns without error
- Slack post fails → log the error, do NOT re-raise (message should still be deleted)
- Mock `shared.db`, `shared.secrets`, `requests`

---

### B2d. `functions/cold_storage_mover/handler.py` (Phase 2 skeleton)

**This is a skeleton only.** The schedule is disabled in the SAM template (`Enabled: false`).
Write enough that it runs without crashing, but do not implement the full S3 move logic.

```python
"""Cold Storage Mover — Phase 2, NOT active in MVP.

Moves raw EOB data for completed cases older than 12 months from RDS to S3.
Schedule is disabled until we have 12-month-old completed cases.
"""
import logging
import os

logger = logging.getLogger(__name__)

def handler(event: dict, context) -> None:
    logger.info("Cold storage mover triggered — Phase 2 not yet implemented. No-op.")
    # TODO Phase 2: query eob_raw for completed cases > 12 months old,
    # upload to s3://roa-cold-storage-prod/{case_id}/{fhir_resource_id}.enc,
    # delete from eob_raw after confirmed upload, write audit log.
```

No tests required for the skeleton.

---

## Track C — Infra Hardening

All changes go in the `infra/` templates. These are polish items — apply them after Track B2
handlers are written and tested. They do not block SAM deploy but should be done before
the project is considered production-ready.

### C1. CloudWatch Log Retention

Add log retention to each function template. Insert after the function's `Tags` block:

```yaml
# In each function template (fhir-processor.yaml, payer-health-check.yaml, etc.)
# Add a LogGroup resource to control retention:

FhirProcessorLogGroup:
  Type: AWS::Logs::LogGroup
  Properties:
    LogGroupName: !Sub /aws/lambda/roa-fhir-processor-${Environment}
    RetentionInDays: !If
      - IsProd
      - 365
      - !If
        - IsStaging
        - 90
        - 30    # dev
```

Add conditions to each template that needs them:
```yaml
Conditions:
  IsProd: !Equals [!Ref Environment, prod]
  IsStaging: !Equals [!Ref Environment, staging]
```

Apply this pattern to: `fhir-processor.yaml`, `payer-health-check.yaml`, `token-refresh.yaml`,
`dlq-alerter.yaml`. (cold-storage-mover.yaml is Phase 2 — skip.)

### C2. Resource Tagging

All SAM resources should carry consistent tags for cost allocation and HIPAA audit trail.
Add a `Globals` tag block in `infra/template.yaml` under the existing `Globals:` section:

```yaml
Globals:
  Function:
    Runtime: python3.12
    Architectures:
      - x86_64
    Tags:
      Project: roa
      HIPAA: "yes"
      Environment: !Ref Environment
```

Also tag the SQS queues in `infra/fhir-processor.yaml` (already have `Environment` tag —
add `Project: roa` and `HIPAA: "yes"` to the existing Tags lists).

### C3. VPC Endpoints

Add VPC endpoints for Secrets Manager and SQS so Lambda traffic stays on the AWS backbone
(no NAT egress charges, lower latency, no public internet path for secrets).

Add to `infra/template.yaml` under Resources (before the Outputs section):

```yaml
  # ── VPC Endpoints ─────────────────────────────────────────────────────────
  # Keep Lambda traffic on the AWS backbone for Secrets Manager and SQS.
  # Eliminates NAT gateway charges for secret lookups (~100 calls/day).

  SecretsManagerVpcEndpoint:
    Type: AWS::EC2::VPCEndpoint
    Properties:
      VpcId: !Ref VpcId
      ServiceName: !Sub com.amazonaws.${AWS::Region}.secretsmanager
      VpcEndpointType: Interface
      SubnetIds: !Split [",", !Join [",", !Ref PrivateSubnetIds]]
      SecurityGroupIds:
        - !Ref LambdaSecurityGroupId
      PrivateDnsEnabled: true

  SqsVpcEndpoint:
    Type: AWS::EC2::VPCEndpoint
    Properties:
      VpcId: !Ref VpcId
      ServiceName: !Sub com.amazonaws.${AWS::Region}.sqs
      VpcEndpointType: Interface
      SubnetIds: !Split [",", !Join [",", !Ref PrivateSubnetIds]]
      SecurityGroupIds:
        - !Ref LambdaSecurityGroupId
      PrivateDnsEnabled: true
```

**Note:** `VpcId` is already declared as a Parameter in `infra/template.yaml`.
VPC endpoints need the actual VPC ID — confirm this is passed via samconfig.toml
(it is — `VpcId=FILL_IN_VPC_ID_*` placeholders exist in all three environments).

### C4. Lambda Code Signing (Optional — implement only if time permits)

Code signing prevents unsigned Lambda packages from being deployed. This requires creating a
signing profile in AWS Signer first (one-time, done by human). If the signing profile ARN is
available, add to each function in the nested stacks:

```yaml
CodeSigningConfigArn: !Sub arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:code-signing-config:...
```

**Skip this if the signing profile hasn't been set up yet** — it's the lowest-priority
hardening item and requires human AWS console action first.

---

## What NOT to Do

- Do not write or modify anything in `shared/` — that is Agent 1's responsibility
- Do not write or modify `functions/fhir_processor/` — that is Agent 1's responsibility
- Do not modify any files in `scripts/` or `.github/workflows/` — already complete
- Do not modify the existing SAM resource definitions (SQS, Lambda configs, alarms) —
  only ADD the new hardening resources (log groups, tags, VPC endpoints)
- Do not implement the actual cold storage move logic — skeleton only

---

## Dependency Note

Your handlers import from `shared/`. If Agent 1 has not yet pushed `shared/secrets.py`,
`shared/logging.py`, `shared/payer_registry.py`, and `shared/audit.py`, your unit tests
should mock those imports:

```python
# In your test files:
from unittest.mock import patch, MagicMock

@patch("functions.payer_health_check.handler.get_payer_config")
@patch("functions.payer_health_check.handler.list_payers")
@patch("functions.payer_health_check.handler.get_connection")
@patch("functions.payer_health_check.handler.get_secret")
def test_healthy_payer(mock_get_secret, mock_get_connection, mock_list_payers, mock_get_payer_config):
    ...
```

This means your handlers and tests can be written and tested independently of Agent 1's output.
The real integration happens when both agents' code exists in the same repo and `make test` runs.

---

## Definition of Done (Track B2 + C)

- [ ] `make test` passes with zero failures (all new tests green)
- [ ] `make lint` passes (ruff + black + mypy) with no errors
- [ ] All four `functions/*/handler.py` files exist and pass their unit tests
- [ ] `functions/cold_storage_mover/handler.py` skeleton exists (no-op, no crash)
- [ ] All four `functions/*/requirements.txt` files contain `requests`
- [ ] Log retention resources added to all 4 active function templates
- [ ] Resource tags (`Project: roa`, `HIPAA: "yes"`) added to all SAM resources
- [ ] VPC endpoints for Secrets Manager and SQS added to `infra/template.yaml`
- [ ] No PHI appears in any log output from the test suite
- [ ] `git push` to `main` — CI green
