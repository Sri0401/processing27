# Architecture Decision Records

Short records of significant decisions: the context, what was decided, and what it costs. Format: [Michael Nygard](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

| # | Decision | Status |
|---|---|---|
| [0001](0001-local-first-aws-parity.md) | Build for AWS, run locally on open-source stand-ins | Accepted |
| [0002](0002-backfill-vs-stream.md) | Load history in bulk; send only live data through the device path | Accepted |
| [0003](0003-timestamps-and-units.md) | Store UTC, report in Europe/London; all energy in kWh | Accepted |
| [0004](0004-data-quality-rules.md) | Flag suspect readings in dbt and calibrate the rules on the data | Accepted |
| [0005](0005-layering-and-access-control.md) | Layered schemas with least-privilege roles; BI sees marts only | Accepted |
| [0006](0006-dashboards-as-code.md) | Generate Grafana dashboards from code; Metabase for ad-hoc work | Accepted |
| [0007](0007-time-series-storage.md) | TimescaleDB locally, native partitioning on Aurora | Accepted |
