# 0002: Load history in bulk; send only live data through the device path

**Status:** Accepted

## Context
BDG2 provides two years of hourly history (~4.4M readings for the site). A real Smart Campus platform has the same shape: years of history in a legacy BMS historian, plus live telemetry from now on.

## Decision
- **History (before 2017-07-01)** is bulk-loaded with `COPY` into a temp table, then `INSERT … ON CONFLICT DO NOTHING` (`smart_campus.load backfill`). That's 3.28M rows in about 2 minutes.
- **Live data (from 2017-07-01)** is replayed by the simulator through MQTT → ingest handler → upsert, exactly as a device would send it.
- Both write to the same table, `raw.meter_reading`, tagged with `source = backfill | stream`.

## Consequences
- The backfill is re-runnable and **never overwrites** streamed values (`DO NOTHING`). The stream **does** overwrite (`DO UPDATE … WHERE value IS DISTINCT FROM`), so a device can correct a reading.
- Pushing history through MQTT would have taken hours, and would have tested the broker more than the pipeline.
- The `source` column lets data-quality reporting compare the two paths (e.g. anomaly rates for stream vs backfill).
