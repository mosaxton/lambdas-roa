# roa-lambdas

Python Lambdas for the Right of Access SSDI app — FHIR EOB processing, payer health checks,
token refresh, DLQ alerting. Companion to [APP.RightOfAccess-Prod](https://github.com/mosaxton/APP.RightOfAccess-Prod.git).

## Requirements

- Python 3.12
- Docker (for local Postgres via docker-compose)
- AWS SAM CLI
- make

## Quickstart

```bash
git submodule update --init --recursive
make setup
make test
```

## Project layout

```
roa-lambdas/
├── shared/              # Lambda Layer — shared code (encryption, db, etc.)
├── functions/           # One directory per Lambda function
│   ├── fhir_processor/
│   ├── payer_health_check/
│   ├── token_refresh/
│   ├── dlq_alerter/
│   └── cold_storage_mover/
├── infra/               # SAM templates and parameter files (added Step 9)
│   └── parameters/      # dev / staging / prod JSON config
├── scripts/             # Utility scripts
├── events/              # Sample Lambda event payloads for local testing
└── vendor/
    └── app.rightofaccess/   # git submodule — web app repo (schema reference)
```

## AWS Infrastructure (updated April 15, 2026)

### Region
us-east-1

### VPC
- VPC ID: vpc-093e04134bcd821d6 (roa-vpc, 10.0.0.0/16)
- Private Subnet 1A: subnet-04bce2c821af36128 (10.0.1.0/24, us-east-1a)
- Private Subnet 1B: subnet-05ddd546e1b145e62 (10.0.2.0/24, us-east-1b)
- NAT Gateway in public subnet for Lambda egress

### Security Groups
- roa-lambda-sg: sg-0cc4844bc547781d2
  - No inbound
  - Egress: TCP 5432 → roa-rds-sg, TCP 443 → 0.0.0.0/0
- roa-rds-sg: sg-012f20a9c3a4af754
  - Inbound: TCP 5432 from roa-lambda-sg, roa-bastion-sg, Moses's IP
  - No egress
- VPC Endpoint SG: created in template.yaml (not manually)
  - Inbound: TCP 443 from roa-lambda-sg

### RDS
- Instance: roa-phi-db-v2
- Endpoint: roa-phi-db-v2.cmv6kmkuyu20.us-east-1.rds.amazonaws.com
- Port: 5432
- Engine: PostgreSQL 18.2
- Class: db.t4g.micro, gp3, 20GB
- Private subnet, NOT publicly accessible
- Deletion protection ON
- OLD roa-phi-db has been DELETED — do not reference it

### Secrets Manager (real names, do not change)
- roa/database-url — direct RDS connection string (NOT the tunnel URL)
- roa/encryption-key — AES-256-GCM key for PHI columns
- roa/bb-client-id — Blue Button sandbox client ID
- roa/bb-client-secret — Blue Button sandbox client secret
- roa/slack-webhook-url — placeholder until Slack webhook created

### S3 Buckets
- roa-sam-artifacts-prod — SAM deployment artifacts
- roa-cold-storage-prod — EOB/audit archive, Glacier IR after 90 days
- roa-phi-records-prod — pre-existing

### SAM Config
- samconfig.toml is filled in with real IDs
- Dev deploys stack "roa-lambdas-dev", sandbox BB
- Prod deploys stack "roa-lambdas-prod", production BB
- Both use the same VPC/subnets/SG (single infrastructure)

### Critical rules for Lambda code
- Lambdas connect to RDS DIRECTLY via VPC (not through SSM tunnel)
- DATABASE_URL in Secrets Manager uses the direct endpoint, not localhost
- Never log PHI to CloudWatch — caseId and payerSlug only
- SQS messages contain {caseId, payerSlug} only, never PHI
- All PHI columns use AES-256-GCM encryption via roa/encryption-key
- Secrets are read from Secrets Manager at runtime, never from env vars

## See also

Plan file in the web repo: `docs/superpowers/plans/roa-lambdas-standalone-project.md`
