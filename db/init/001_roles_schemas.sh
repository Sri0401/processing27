#!/bin/bash
# Runs once, on first start of an empty database volume.
# Creates least-privilege roles and the layered schemas:
#   raw          <- written only by ingest_writer (pipelines)
#   staging,
#   intermediate,
#   marts        <- owned by transformer (dbt)
#   marts        -> read only by bi_reader (Grafana / Metabase)
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  CREATE EXTENSION IF NOT EXISTS timescaledb;

  CREATE ROLE ingest_writer LOGIN PASSWORD '${INGEST_DB_PASSWORD}';
  CREATE ROLE transformer   LOGIN PASSWORD '${TRANSFORM_DB_PASSWORD}';
  CREATE ROLE bi_reader     LOGIN PASSWORD '${BI_DB_PASSWORD}';
  -- On managed PostgreSQL (Aurora, Azure) the admin is not a superuser: it needs membership
  -- to create schemas owned by transformer and to set default privileges on its behalf.
  GRANT transformer TO ${POSTGRES_USER};

  REVOKE ALL ON DATABASE ${POSTGRES_DB} FROM PUBLIC;
  REVOKE CREATE ON SCHEMA public FROM PUBLIC;
  GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ingest_writer, transformer, bi_reader;
  -- Bulk loads stage through temp tables; dbt incremental models use them too.
  GRANT TEMPORARY ON DATABASE ${POSTGRES_DB} TO ingest_writer, transformer;

  CREATE SCHEMA raw;
  CREATE SCHEMA staging      AUTHORIZATION transformer;
  CREATE SCHEMA intermediate AUTHORIZATION transformer;
  CREATE SCHEMA marts        AUTHORIZATION transformer;
  CREATE SCHEMA seeds        AUTHORIZATION transformer;

  GRANT USAGE ON SCHEMA raw TO ingest_writer, transformer;
  ALTER DEFAULT PRIVILEGES IN SCHEMA raw
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO ingest_writer;
  ALTER DEFAULT PRIVILEGES IN SCHEMA raw
    GRANT USAGE, SELECT ON SEQUENCES TO ingest_writer;
  ALTER DEFAULT PRIVILEGES IN SCHEMA raw
    GRANT SELECT ON TABLES TO transformer;

  -- BI users only ever see curated marts, never raw telemetry.
  GRANT USAGE ON SCHEMA marts TO bi_reader;
  ALTER DEFAULT PRIVILEGES FOR ROLE transformer IN SCHEMA marts
    GRANT SELECT ON TABLES TO bi_reader;

  -- Platform health views (raw.v_*) are exposed to Grafana explicitly in 002.
  GRANT USAGE ON SCHEMA raw TO bi_reader;
SQL
