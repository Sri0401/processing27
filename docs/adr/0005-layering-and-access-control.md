# 0005: Layered schemas with least-privilege roles; BI sees marts only

**Status:** Accepted

## Context
One sole data engineer, several audiences: facilities, estates, finance, and contractors who may get dashboard access. Raw telemetry may later include occupancy-derived data (e.g. Wi-Fi counts), which is personal data under UK GDPR.

## Decision
| Schema | Contents | Written by | Read by |
|---|---|---|---|
| `raw` | Data as received + load metadata | `ingest_writer` | `transformer` |
| `seeds` | Editable reference data (tariffs, carbon factors, schedules, SLAs) | `transformer` (dbt seed) | `transformer` |
| `staging` / `intermediate` | Typed, renamed, flagged | `transformer` (dbt) | `transformer` |
| `marts` | Business-ready facts, dimensions and KPIs | `transformer` (dbt) | `bi_reader` |

- Roles are created in `db/init/001_roles_schemas.sh`. `PUBLIC` loses all default rights.
- `bi_reader` can only see `marts` plus two aggregate health views. It never sees raw telemetry.
- The admin password never reaches job containers: loaders run as `ingest_writer`, dbt as `transformer`.
- In AWS, these credentials live in Secrets Manager. The Lambda reads only the `ingest_writer` secret.

## Consequences
- A new dashboard or tool needs a mart, not a raw-table grant. That keeps business logic in dbt, where it is tested and documented.
- dbt docs (`make dbt-docs`) are the data dictionary: every mart has a description, and key columns have tests and definitions.
