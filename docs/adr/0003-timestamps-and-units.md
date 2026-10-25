# 0003: Store UTC, report in Europe/London; all energy in kWh

**Status:** Accepted

## Context
BDG2 timestamps are hourly local *standard* time with no daylight-saving shifts: each meter has exactly 17,544 rows, which is 731 days × 24. For a UK site, local standard time is GMT, which equals UTC. Occupancy, tariffs and academic terms are defined in local clock time.

## Decision
- Store every timestamp as `timestamptz` in **UTC** (BDG2 values are tagged UTC on extract).
- Convert to **Europe/London** once, in `fct_energy_hourly` (`local_ts`, `local_date`, `local_hour`). All occupancy, term and tariff logic uses local time, so BST is handled automatically.
- Accept **kWh only** in the telemetry contract (`unit` must be `kWh`). Hourly kWh equals average kW, which is how baseload and peak are expressed.

## Consequences
- In summer, local hours are UTC + 1. A building scheduled 08:00–18:00 is evaluated correctly in both seasons.
- New meter types with other units (e.g. water in m³) need a contract version bump and a unit column in the marts.
