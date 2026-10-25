# Runbook

Operating procedures for the platform. Commands are local (`make …`); AWS equivalents are noted.

## Daily operation

| When | What | Command |
|---|---|---|
| Hourly (scheduled) | Refresh models incrementally | `make dbt` (AWS: scheduled ECS task / CodeBuild) |
| Hourly | Check telemetry freshness (warn > 2 h, error > 48 h) | `make dbt-fresh` |
| Weekly | Review *Platform Health*: rejects, flagged meters, completeness | Grafana |

## Alerts and what to do

### "Telemetry silence": no readings for 30 minutes
1. *Platform Health → Ingestion throughput*: did everything stop, or one building?
2. Everything: check the broker or IoT Core (`docker compose logs mqtt` / IoT Core monitoring), then the ingest logs (`docker compose logs ingest` / CloudWatch Logs).
3. One building: the gateway is offline. Contact the BMS contractor with the building name.

### Spike in rejected messages
1. *Platform Health → Rejections by reason*.
2. Inspect examples: `select reason, payload from raw.telemetry_reject order by received_at desc limit 20;`
3. `negative_consumption` or `value_out_of_range` from one building usually means a meter fault, so raise a work order. `missing_field`, `unparseable_json` or `unsupported_schema_version` usually means an integration change, so contact the supplier.

### Ingest errors / throttles (AWS alarm)
- Throttles: reserved concurrency (20) protects Aurora connections. Sustained throttling means more devices: raise the limit together with Aurora max ACUs.
- Errors: CloudWatch Logs for the function. Messages the rule could not deliver are in `s3://<raw-bucket>/iot-errors/` and can be replayed.

### dbt test failure
`make dbt` stops downstream models when a test fails, so dashboards keep the last good data. Read the failing test, fix the data or the rule, and re-run. Never delete a test to get a green build.

## Recovery

| Situation | Procedure |
|---|---|
| Database was down | Nothing to do: the bridge retries with backoff. AWS: IoT error action → S3, then replay |
| Bad data was loaded | Correct readings by re-sending them (upsert overwrites), then `dbt build --full-refresh -s int_meter_readings_flagged+` |
| Rebuild everything from scratch | `make nuke && make demo` (about 10 minutes) |
| Replay from the raw archive | Raw JSON Lines in `s3://<raw-bucket>/telemetry/dt=YYYY-MM-DD/` can be re-sent through the handler |

## Change procedures

- **Add a building or meter:** add it to the building metadata, then provision an IoT Thing named after the building (the IoT policy scopes its topic automatically). No pipeline change is needed.
- **Change a tariff or carbon factor:** edit the seed CSV, open a PR, and `make dbt` after merge.
- **Change a dashboard:** edit `dashboards/build_dashboards.py`, then `make dashboards` (regenerates and smoke-tests). Commit the generated JSON.
- **Change the telemetry contract:** bump `SCHEMA_VERSION`, accept both versions during the migration, and announce it to integrators first.
