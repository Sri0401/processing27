# Smart Campus OT Data Platform - task runner. Run from WSL / Linux / macOS.
#   make demo     -> everything, end to end, from an empty machine
SHELL := /bin/bash
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)
COMPOSE := docker compose
TOOLS := $(COMPOSE) run --rm tools

.PHONY: help env up down nuke build data extract load backfill synthetic stream stream-bg \
        dbt dbt-fresh dbt-docs dashboards dashboards-build dashboards-check test lint bi demo psql

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

BACKFILL_UNTIL ?= 2017-07-01
backfill:  ## Bulk-load history before the stream start date (BACKFILL_UNTIL=YYYY-MM-DD)
	$(TOOLS) python -m smart_campus.load backfill --until $(BACKFILL_UNTIL)

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

dashboards: dashboards-build dashboards-check  ## Regenerate dashboards, smoke-test their SQL, reload Grafana
	$(COMPOSE) restart grafana

dashboards-build:  ## Generate Grafana dashboard JSON from code
	$(TOOLS) python dashboards/build_dashboards.py

dashboards-check:  ## Run every dashboard query as the read-only bi_reader role
	$(TOOLS) python dashboards/check_dashboards.py

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

# ---------------------------------------------------------------------------- Azure
# Infrastructure: infra-azure/deploy.sh (needs the Azure CLI). These targets run the data
# steps against the Azure PostgreSQL server using the same tools container, over TLS.
-include .azure/secrets.env
-include .azure/outputs.env
AZ_DB := -e DB_HOST=$(AZ_DB_HOST) -e DB_SSLMODE=require
AZ_HOURS ?= 24
AZ_START ?= 2017-07-01T00:00:00Z

.PHONY: azure-infra azure-apps azure-down azure-require azure-bootstrap azure-load azure-dbt \
        azure-check azure-stream azure-demo

azure-infra:  ## Azure: deploy resource group, PostgreSQL, Storage, Key Vault, logs, budget
	./infra-azure/deploy.sh infra

azure-apps:  ## Azure: deploy the ingest API + Grafana container apps (images from GHCR)
	./infra-azure/deploy.sh apps

azure-down:  ## Azure: delete everything (asks for confirmation)
	./infra-azure/deploy.sh down

azure-require:
	@test -n "$(AZ_DB_HOST)" || { echo "No .azure/outputs.env - run infra-azure/deploy.sh infra first"; exit 1; }

azure-bootstrap: azure-require  ## Azure: create database, roles, schemas, raw tables
	@$(COMPOSE) run --rm -e PGHOST=$(AZ_DB_HOST) -e PGUSER=platform_admin -e PGSSLMODE=require \
		-e PGPASSWORD=$(AZ_PG_ADMIN_PASSWORD) -e INGEST_DB_PASSWORD=$(AZ_INGEST_DB_PASSWORD) \
		-e TRANSFORM_DB_PASSWORD=$(AZ_TRANSFORM_DB_PASSWORD) -e BI_DB_PASSWORD=$(AZ_BI_DB_PASSWORD) \
		tools bash scripts/bootstrap_remote_db.sh

azure-load: azure-require  ## Azure: load reference data, history and synthetic estates data
	@$(COMPOSE) run --rm $(AZ_DB) -e DB_PASSWORD=$(AZ_INGEST_DB_PASSWORD) tools bash -c "\
		python -m smart_campus.load reference && \
		python -m smart_campus.load backfill --until $(BACKFILL_UNTIL) && \
		python -m smart_campus.load synthetic"

azure-dbt: azure-require  ## Azure: dbt build against the Azure warehouse
	@$(COMPOSE) run --rm $(AZ_DB) -e TRANSFORM_DB_PASSWORD=$(AZ_TRANSFORM_DB_PASSWORD) -e DBT_THREADS=2 \
		tools bash -c "cd warehouse/dbt && dbt build"

azure-check: azure-require  ## Azure: run every dashboard query as bi_reader
	@$(COMPOSE) run --rm $(AZ_DB) -e BI_DB_PASSWORD=$(AZ_BI_DB_PASSWORD) tools python dashboards/check_dashboards.py

azure-stream: azure-require  ## Azure: replay AZ_HOURS of telemetry as gateway batches to the ingest API
	@test -n "$(AZ_INGEST_URL)" || { echo "Ingest API not deployed - run azure-apps first"; exit 1; }
	@$(COMPOSE) --profile sim run --rm -e INGEST_API_KEY=$(AZ_INGEST_API_KEY) simulator \
		python -m smart_campus.simulator --http-endpoint $(AZ_INGEST_URL)/telemetry \
		--start $(AZ_START) --hours $(AZ_HOURS) --speedup 0.5

azure-demo: azure-bootstrap azure-load azure-dbt azure-check  ## Azure: all data steps after deploy
	@echo "Grafana: $(AZ_GRAFANA_URL)"

# ---------------------------------------------------------------------------- AWS (brief demo)
# Infrastructure: scripts/aws_demo.sh (needs the AWS CLI + Node). These targets run the data
# steps against Aurora from the tools container. See docs/adr/0009-aws-demo-deployment.md.
-include .aws-demo/outputs.env
-include .aws-demo/secrets.env
AWSD_DB := -e DB_HOST=$(AWSD_DB_HOST) -e DB_SSLMODE=require

.PHONY: lambda-layer aws-require aws-bootstrap aws-load aws-dbt aws-check aws-stream aws-grafana aws-demo

lambda-layer:  ## Build the ingest Lambda's arm64 dependency layer (build/ingest-deps-arm64.zip)
	$(TOOLS) bash -c "rm -rf build/layer build/ingest-deps-arm64.zip && \
		pip install --quiet --platform manylinux2014_aarch64 --only-binary=:all: --python-version 3.12 \
			--implementation cp --target build/layer/python 'psycopg[binary]==3.2.3' && \
		cd build/layer && python -m zipfile -c ../ingest-deps-arm64.zip python && \
		ls -l ../ingest-deps-arm64.zip"

aws-require:
	@test -n "$(AWSD_DB_HOST)" || { echo "No .aws-demo/outputs.env - run scripts/aws_demo.sh up first"; exit 1; }

aws-bootstrap: aws-require  ## AWS: create roles, schemas and raw tables on Aurora
	@$(COMPOSE) run --rm -e PGHOST=$(AWSD_DB_HOST) -e PGUSER=platform_admin -e PGSSLMODE=require \
		-e PGPASSWORD=$(AWSD_ADMIN_PASSWORD) -e INGEST_DB_PASSWORD=$(AWSD_INGEST_DB_PASSWORD) \
		-e TRANSFORM_DB_PASSWORD=$(AWSD_TRANSFORM_DB_PASSWORD) -e BI_DB_PASSWORD=$(AWSD_BI_DB_PASSWORD) \
		tools bash scripts/bootstrap_remote_db.sh

aws-load: aws-require  ## AWS: load reference data, history and synthetic estates data into Aurora
	@$(COMPOSE) run --rm $(AWSD_DB) -e DB_PASSWORD=$(AWSD_INGEST_DB_PASSWORD) tools bash -c "\
		python -m smart_campus.load reference && \
		python -m smart_campus.load backfill --until $(BACKFILL_UNTIL) && \
		python -m smart_campus.load synthetic"

aws-dbt: aws-require  ## AWS: dbt build against Aurora
	@$(COMPOSE) run --rm $(AWSD_DB) -e TRANSFORM_DB_PASSWORD=$(AWSD_TRANSFORM_DB_PASSWORD) -e DBT_THREADS=2 \
		tools bash -c "cd warehouse/dbt && dbt build"

aws-check: aws-require  ## AWS: run every dashboard query against Aurora as bi_reader
	@$(COMPOSE) run --rm $(AWSD_DB) -e BI_DB_PASSWORD=$(AWSD_BI_DB_PASSWORD) tools python dashboards/check_dashboards.py

AWS_HOURS ?= 24
aws-stream: aws-require  ## AWS: replay AWS_HOURS of telemetry to IoT Core over MQTT/TLS with the gateway cert
	@test -f .aws-demo/gateway.cert.pem || { echo "No gateway certificate - run scripts/aws_demo.sh cert"; exit 1; }
	@$(COMPOSE) --profile sim run --rm -v ./.aws-demo:/app/.aws-demo:ro \
		-e MQTT_HOST=$(AWSD_IOT_ENDPOINT) -e MQTT_PORT=8883 -e MQTT_CLIENT_ID=$(AWSD_GATEWAY_THING) \
		-e MQTT_TLS_CERT=/app/.aws-demo/gateway.cert.pem -e MQTT_TLS_KEY=/app/.aws-demo/gateway.private.key \
		-e MQTT_MAX_RATE=50 simulator \
		python -m smart_campus.simulator --start $(AZ_START) --hours $(AWS_HOURS) --speedup 0.1

aws-grafana: aws-require  ## AWS: local Grafana on :3002 reading the Aurora warehouse
	@$(COMPOSE) run --rm -d --name grafana-aurora -p 3002:3000 -e CAMPUS_DB_HOST=$(AWSD_DB_HOST) \
		-e CAMPUS_DB_SSLMODE=require -e BI_DB_PASSWORD=$(AWSD_BI_DB_PASSWORD) grafana >/dev/null
	@echo "Grafana (Aurora): http://localhost:3002   stop with: docker rm -f grafana-aurora"

aws-demo: aws-bootstrap aws-load aws-dbt aws-check  ## AWS: all data steps after scripts/aws_demo.sh up
