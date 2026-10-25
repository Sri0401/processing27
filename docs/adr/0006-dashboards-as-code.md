# 0006: Generate Grafana dashboards from code; Metabase for ad-hoc work

**Status:** Accepted

## Context
Dashboards are the product for non-technical colleagues. Edited by hand in a UI, they drift, break silently when a model changes, and can't be reviewed.

## Decision
- `dashboards/build_dashboards.py` generates the Grafana JSON from compact panel specs. Grafana provisions the files read-only (`allowUiUpdates: false`).
- `dashboards/check_dashboards.py` runs every panel's SQL **as `bi_reader`** in CI, catching broken SQL, missing columns and missing grants before stakeholders do. CI also fails if the generated JSON is not committed.
- Three dashboards, one per audience: *Campus Energy* (facilities, sustainability), *Estates* (asset managers, capital planning), *Platform Health* (data engineer, BMS contractor).
- Design rules: one fixed colour per meter everywhere, status colours reserved for RAG and always shown with a text label, no dual axes, a plain-English description on every panel, and days with < 80% meter coverage shown as gaps.
- **Metabase** (optional, `make bi`) gives analysts ad-hoc, self-service exploration of the same `marts` schema.

## Consequences
- Dashboard changes go through pull requests like any other code.
- Lesson learned: Grafana silently draws an empty plot for an invalid legend `placement` value. The smoke test checks the SQL, not the rendering, so new panel types should still be checked visually once.
