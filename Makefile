# Smart Campus OT Data Platform - task runner. Run from WSL / Linux / macOS.
#   make demo     -> everything, end to end, from an empty machine
SHELL := /bin/bash
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)
COMPOSE := docker compose
TOOLS := $(COMPOSE) run --rm tools

.PHONY: help env up down nuke build data extract load backfill synthetic stream stream-bg \
        dbt dbt-fresh dbt-docs test lint bi demo psql

help:  ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

env:  ## Create .env from the template (first run only)
	@test -f .env || (cp .env.example .env && echo "created .env - change the passwords")

build: env  ## Build the platform image
	$(COMPOSE) build ingest

up: env  ## Start core services (db, mqtt, s3, ingest, grafana)
	$(COMPOSE) up -d --build db mqtt s3 ingest grafana

down:  ## Stop services (keeps data)
	$(COMPOSE) --profile sim --profile tools --profile bi down

nuke:  ## Stop services AND delete all volumes
	$(COMPOSE) --profile sim --profile tools --profile bi down -v

data:  ## Download the BDG2 source files (~215 MB)
	./scripts/download_bdg2.sh

extract:  ## BDG2 wide CSV -> tidy Parquet for $$SITE_ID
	$(TOOLS) python -m smart_campus.extract_bdg2

load:  ## Load building metadata + weather into raw
	$(TOOLS) python -m smart_campus.load reference

backfill:  ## Bulk-load history before the stream start date
	$(TOOLS) python -m smart_campus.load backfill --until 2017-07-01

synthetic:  ## Generate + load synthetic estates data (assets, work orders, audits)
	$(TOOLS) python -m smart_campus.synthetic
	$(TOOLS) python -m smart_campus.load synthetic

stream:  ## Replay telemetry over MQTT (Ctrl+C to stop)
	$(COMPOSE) --profile sim run --rm simulator

stream-bg:  ## Replay telemetry in the background
	$(COMPOSE) --profile sim up -d simulator

dbt:  ## Seed, build and test all dbt models
	$(TOOLS) bash -c "cd warehouse/dbt && dbt build"

dbt-fresh:  ## Check raw telemetry freshness (source freshness SLA)
	$(TOOLS) bash -c "cd warehouse/dbt && dbt source freshness"

dbt-docs:  ## Generate dbt docs (data dictionary + lineage) into warehouse/dbt/target
	$(TOOLS) bash -c "cd warehouse/dbt && dbt docs generate"

test:  ## Unit tests
	$(TOOLS) pytest -q

lint:  ## Ruff lint
	$(TOOLS) ruff check .

bi:  ## Start Metabase (self-service BI) on :3001
	$(COMPOSE) --profile bi up -d metabase

psql:  ## Open psql as admin
	$(COMPOSE) exec db bash -c 'psql -U $$POSTGRES_USER -d $$POSTGRES_DB'

demo: up extract load backfill synthetic dbt  ## Full pipeline from scratch
	@echo "Grafana: http://localhost:3000  |  start live telemetry with: make stream-bg"
