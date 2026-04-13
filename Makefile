.PHONY: setup install compile-deps test lint format seed-db gen-fixtures db-up db-down clean

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
	$(VENV)/bin/pytest

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
	@echo "seed-db: wired in Step 3"

gen-fixtures:
	@echo "gen-fixtures: wired in Step 2"

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache .aws-sam
