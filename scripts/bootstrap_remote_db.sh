#!/usr/bin/env bash
# Create the database, roles, schemas and raw tables on a managed PostgreSQL server
# (Azure Flexible Server / Aurora) - the remote equivalent of db/init on first container start.
# Expects PGHOST, PGUSER, PGPASSWORD, PGSSLMODE plus the role passwords used by db/init.
set -euo pipefail

: "${POSTGRES_DB:=smart_campus}"
export POSTGRES_USER="$PGUSER" POSTGRES_DB

if ! psql -d postgres -tAc "select 1 from pg_database where datname = '$POSTGRES_DB'" | grep -q 1; then
  echo "creating database $POSTGRES_DB"
  psql -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $POSTGRES_DB"
fi

if psql -d "$POSTGRES_DB" -tAc "select 1 from pg_namespace where nspname = 'raw'" | grep -q 1; then
  echo "schema raw already exists - database is bootstrapped, nothing to do"
  exit 0
fi

bash db/init/001_roles_schemas.sh
psql -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f db/init/002_raw_tables.sql
echo "bootstrap complete"
