.PHONY: setup install compile-deps test test-fn lint format seed-db gen-fixtures db-up db-down \
        invoke build deploy-dev deploy-staging deploy-prod smoke-test check-drift clean

PYTHON ?= python3.12
VENV ?= .venv

setup: ## One-time setup: create venv, install deps, start Postgres
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip pip-tools
	$(VENV)/bin/pip-sync requirements.txt requirements-dev.txt
	docker compose up -d postgres

install:
	$(VENV)/bin/pip-sync requirements.txt requirements-dev.txt

compile-deps:
	$(VENV)/bin/pip-compile requirements.in -o requirements.txt
	$(VENV)/bin/pip-compile requirements-dev.in -o requirements-dev.txt

test:
	$(VENV)/bin/pytest -v

## Run tests for a single function: make test-fn FN=fhir_processor
test-fn:
	$(VENV)/bin/pytest -v functions/$(FN)/tests/ shared/tests/

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/black --check .
	$(VENV)/bin/mypy shared functions

format:
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/black .

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

seed-db:
	chmod +x scripts/seed-test-db.sh
	./scripts/seed-test-db.sh

gen-fixtures:
	npx --yes tsx scripts/generate-cross-lang-fixture.ts

## Invoke a Lambda locally: make invoke FN=FhirProcessorFunction EVENT=events/fhir-processor-sqs.json
invoke:
	chmod +x scripts/invoke-local.sh
	./scripts/invoke-local.sh $(FN) $(EVENT)

## SAM build (all functions)
build:
	sam build --config-env $(or $(ENV),dev) --cached --parallel

## Deploy to dev (auto): make deploy-dev
deploy-dev: build
	sam deploy --config-env dev --no-confirm-changeset
	./scripts/smoke-test.sh dev

## Deploy to staging (manual): make deploy-staging
deploy-staging: build
	sam deploy --config-env staging

## Deploy to prod (manual, requires approval): make deploy-prod
deploy-prod: build
	sam deploy --config-env prod

## Post-deploy smoke test: make smoke-test ENV=dev
smoke-test:
	chmod +x scripts/smoke-test.sh
	./scripts/smoke-test.sh $(or $(ENV),dev)

## Check for schema drift between Prisma and Python
check-drift:
	$(VENV)/bin/python scripts/check-schema-drift.py

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache .aws-sam
