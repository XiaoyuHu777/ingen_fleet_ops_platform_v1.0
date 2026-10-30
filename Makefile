# Run these targets from the project root.
.DEFAULT_GOAL := help

COMPOSE := docker compose --env-file .env -f infra/local/compose.yaml
UV ?= $(if $(wildcard .tools/bootstrap/bin/uv),.tools/bootstrap/bin/uv,uv)
RUN := UV_PYTHON_INSTALL_DIR="$(CURDIR)/.tools/python" UV_CACHE_DIR="$(CURDIR)/.uv-cache" $(UV) run --frozen
TFLINT ?= $(if $(wildcard .tools/bin/tflint),.tools/bin/tflint,tflint)
GITLEAKS ?= $(if $(wildcard .tools/bin/gitleaks),.tools/bin/gitleaks,gitleaks)

.PHONY: help up ps logs db down migrate setup lint typecheck test check terraform-check secret-scan openapi-check

help:
	@printf '%s\n' \
	  'make up    Start the local stack and wait for health checks.' \
	  'make ps    Show service status.' \
	  'make logs  Show the last 50 log lines per service.' \
	  'make db    Open the PostgreSQL SQL console.' \
	  'make migrate  Apply registry database migrations.' \
	  'make down  Remove this stack; keep its named data volumes.' \
	  'make setup Install locked Python dependencies with uv.' \
	  'make check Run Python lint, format, types, and tests.' \
	  'make terraform-check Check the no-resource Terraform scaffold.' \
	  'make secret-scan Scan public-safe source files with gitleaks.'

up:
	$(COMPOSE) up -d --build --wait

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=50

db:
	$(COMPOSE) exec postgres sh -c 'exec psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

down:
	$(COMPOSE) down

migrate:
	$(COMPOSE) exec registry alembic upgrade head

setup:
	UV_PYTHON_INSTALL_DIR="$(CURDIR)/.tools/python" UV_CACHE_DIR="$(CURDIR)/.uv-cache" $(UV) sync --frozen

lint:
	$(RUN) ruff check services tests scripts
	$(RUN) black --check services tests scripts

typecheck:
	$(RUN) mypy

test:
	$(RUN) pytest

check: lint typecheck test

terraform-check:
	terraform fmt -check -recursive infra/terraform
	terraform -chdir=infra/terraform init -backend=false -input=false
	terraform -chdir=infra/terraform validate
	$(TFLINT) --chdir=infra/terraform

secret-scan:
	$(GITLEAKS) dir . --config .gitleaks.toml --redact --no-banner

openapi-check:
	$(RUN) python -m scripts.openapi_snapshot --check
