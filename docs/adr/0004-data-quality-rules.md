# 0004: Flag suspect readings in dbt and calibrate the rules on the data

**Status:** Accepted

## Context
Two kinds of bad data arrive:
- **Structurally invalid** (missing fields, negative kWh, bad JSON). These can be judged per message.
- **Plausible but suspect** (spikes, stuck sensors, zero-filled outages). These can only be judged against history.

## Decision
- The ingest contract **rejects** structural problems into `raw.telemetry_reject` (dead letters, with reasons).
- dbt (`int_meter_readings_flagged`) **flags, never deletes**, suspect readings:
  - **Spike:** more than 6σ above the trailing 7-day mean *and* more than 2× that mean (`RANGE` window, so gaps don't distort it).
  - **Stuck sensor:** a run of ≥ 24 identical non-zero hourly values (gaps-and-islands; a missing hour breaks the run).
  - **Outage:** a run of ≥ 168 hours of exactly zero.
- Reporting uses `*_clean` measures that exclude flagged readings. Flag counts are visible on the Platform Health dashboard.
- All thresholds are dbt `vars`, so they can be tuned without code changes.

## How the thresholds were chosen
The first version flagged any run of 6 identical values and excluded **13% of all readings**. Profiling the actual run lengths showed:

| Run length (identical non-zero values) | Readings | Interpretation |
|---|---|---|
| 6–23 h | ~63k | Low-resolution meters legitimately repeating |
| 72 h + | ~412k across 143 buildings | Gap-fill artefacts in BDG2 "cleaned" files |

So the threshold was raised to 24 h, and runs are flagged in full rather than from the Nth hour onwards. Later, a campus-wide drop to zero for weeks in early 2017 turned out to be a source outage, not real consumption, which led to the 168-hour zero rule.

## Consequences
- About 18% of historical readings are flagged, almost all from source gap-fills. This is reported openly, not hidden.
- Some genuine flat loads of 24 h or more (e.g. a constant-load server room) may be flagged. They remain queryable in the intermediate model.
- Per-day charts additionally hide days when fewer than 80% of meters reported (see ADR 0006).
