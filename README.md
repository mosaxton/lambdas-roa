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

## See also

Plan file in the web repo: `docs/superpowers/plans/roa-lambdas-standalone-project.md`
