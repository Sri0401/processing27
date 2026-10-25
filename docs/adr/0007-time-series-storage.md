# 0007: TimescaleDB locally, native partitioning on Aurora

**Status:** Accepted

## Context
`raw.meter_reading` grows by about 2.3M rows per year for one site, and several sites are expected. Locally we use TimescaleDB hypertables. **Aurora PostgreSQL does not support the TimescaleDB extension.**

## Decision
- Keep all transformation and dashboard SQL **portable PostgreSQL**. No dbt model or dashboard uses Timescale functions (`time_bucket`, continuous aggregates); they use `date_trunc`, window functions and ordered-set aggregates.
- The only Timescale-specific statement is `create_hypertable` in `db/init/002_raw_tables.sql`. On Aurora, that line is replaced by native declarative range partitioning on `ts` (monthly), managed by `pg_partman`, which Aurora supports.

## Consequences
- Moving to Aurora changes one DDL statement, not the models.
- We give up Timescale compression and continuous aggregates. At this volume, partition pruning plus the dbt daily/annual marts are enough. Revisit if the hourly fact grows past roughly 100M rows, e.g. by moving cold partitions to S3 as Parquet and querying them with Athena.
