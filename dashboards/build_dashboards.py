"""Dashboards as code: generates Grafana dashboard JSON from compact panel specs.

    python dashboards/build_dashboards.py      # writes dashboards/grafana/dashboards/*.json

Grafana provisions the generated files read-only (allowUiUpdates: false), so every
dashboard change goes through version control and review, like any other code.

Design rules applied throughout (see docs/dashboard-design.md):
  * each meter keeps one fixed colour everywhere (electricity blue, gas orange)
  * status colours (green/amber/red) are reserved for RAG and always shown with a label
  * one y-axis per chart; headline numbers are stat tiles, not charts
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "grafana" / "dashboards"
DS = {"type": "grafana-postgresql-datasource", "uid": "campus-pg"}

SERIES = {"electricity": "#2a78d6", "gas": "#eb6834", "reactive": "#eb6834", "planned": "#2a78d6",
          "weekday": "#2a78d6", "weekend": "#1baf7a"}
STATUS = {"good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b"}
NEUTRAL = "#52514e"


class Layout:
    """Places panels left-to-right on Grafana's 24-column grid."""

    def __init__(self):
        self.x = self.y = self.row_h = 0
        self.next_id = 1

    def place(self, w: int, h: int) -> dict:
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def new_row(self) -> None:
        if self.x:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0

    def id(self) -> int:
        self.next_id += 1
        return self.next_id


def target(sql: str, fmt: str = "table") -> list[dict]:
    return [{"refId": "A", "datasource": DS, "editorMode": "code", "format": fmt,
             "rawQuery": True, "rawSql": " ".join(sql.split())}]


def color_overrides(names) -> list[dict]:
    return [{"matcher": {"id": "byName", "options": n},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": SERIES[n]}}]}
            for n in names]


def text_panel(lay: Layout, content: str, w: int = 24, h: int = 3) -> dict:
    return {"id": lay.id(), "type": "text", "title": "", "gridPos": lay.place(w, h),
            "options": {"mode": "markdown", "content": content}}


def stat(lay: Layout, title: str, sql: str, unit: str, description: str, w: int = 6, h: int = 4,
         decimals: int = 0, thresholds: list[dict] | None = None) -> dict:
    steps = thresholds or [{"color": NEUTRAL, "value": None}]
    return {
        "id": lay.id(), "type": "stat", "title": title, "description": description, "datasource": DS,
        "gridPos": lay.place(w, h), "targets": target(sql),
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals,
                                     "color": {"mode": "thresholds"},
                                     "thresholds": {"mode": "absolute", "steps": steps}},
                        "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value", "graphMode": "none", "textMode": "value",
                    "justifyMode": "center", "wideLayout": True},
    }


# Grafana does not fill these in for provisioned panels; without them uPlot draws no axes.
AXIS_DEFAULTS = {"axisPlacement": "auto", "axisBorderShow": False, "axisSoftMin": 0,
                 "scaleDistribution": {"type": "linear"},
                 "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                 "thresholdsStyle": {"mode": "off"}}


def timeseries(lay: Layout, title: str, sql: str, unit: str, description: str, series: list[str],
               w: int = 24, h: int = 8, bars: bool = False, stacked: bool = False) -> dict:
    custom = {**AXIS_DEFAULTS, "drawStyle": "bars" if bars else "line", "lineWidth": 2,
              "fillOpacity": 85 if bars else 0, "showPoints": "never", "pointSize": 4, "barAlignment": 0,
              "lineInterpolation": "linear", "stacking": {"mode": "normal" if stacked else "none", "group": "A"},
              "gradientMode": "none", "spanNulls": False}
    return {
        "id": lay.id(), "type": "timeseries", "title": title, "description": description, "datasource": DS,
        "gridPos": lay.place(w, h), "targets": target(sql, "time_series"),
        "fieldConfig": {"defaults": {"unit": unit, "custom": custom,
                                     "color": {"mode": "fixed", "fixedColor": SERIES["electricity"]}},
                        "overrides": color_overrides(series)},
        "options": {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom", "calcs": []},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
    }


def barchart(lay: Layout, title: str, sql: str, unit: str, description: str, series: list[str],
             w: int = 12, h: int = 8, horizontal: bool = False, stacked: bool = False) -> dict:
    return {
        "id": lay.id(), "type": "barchart", "title": title, "description": description, "datasource": DS,
        "gridPos": lay.place(w, h), "targets": target(sql),
        "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "fixed", "fixedColor": SERIES["electricity"]},
                                     "custom": {**AXIS_DEFAULTS, "lineWidth": 0, "fillOpacity": 90,
                                                "gradientMode": "none"}},
                        "overrides": color_overrides(series)},
        "options": {"orientation": "horizontal" if horizontal else "vertical", "barWidth": 0.8,
                    "groupWidth": 0.75, "stacking": "normal" if stacked else "none",
                    "showValue": "never", "xTickLabelRotation": 0, "xTickLabelSpacing": 60,
                    "legend": {"showLegend": len(series) > 1, "displayMode": "list", "placement": "bottom", "calcs": []},
                    "tooltip": {"mode": "multi"}},
    }


def table(lay: Layout, title: str, sql: str, description: str, columns: dict[str, dict],
          w: int = 12, h: int = 10, sort: str | None = None) -> dict:
    """columns: {column_name: {"unit": ..., "decimals": ..., "rag": True, "label": ...}}"""
    overrides = []
    for name, spec in columns.items():
        props = []
        if "label" in spec:
            props.append({"id": "displayName", "value": spec["label"]})
        if "unit" in spec:
            props.append({"id": "unit", "value": spec["unit"]})
        if "decimals" in spec:
            props.append({"id": "decimals", "value": spec["decimals"]})
        if "width" in spec:
            props.append({"id": "custom.width", "value": spec["width"]})
        if spec.get("rag"):
            props += [
                {"id": "mappings", "value": [{"type": "value", "options": {
                    "Red": {"color": STATUS["critical"], "text": "● Red"},
                    "Amber": {"color": STATUS["warning"], "text": "● Amber"},
                    "Green": {"color": STATUS["good"], "text": "● Green"}}}]},
                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            ]
        if spec.get("bar"):
            props += [{"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic"}},
                      {"id": "color", "value": {"mode": "fixed", "fixedColor": SERIES["electricity"]}},
                      {"id": "max", "value": spec.get("max", 1)}, {"id": "min", "value": 0}]
        overrides.append({"matcher": {"id": "byName", "options": name}, "properties": props})
    return {
        "id": lay.id(), "type": "table", "title": title, "description": description, "datasource": DS,
        "gridPos": lay.place(w, h), "targets": target(sql),
        "fieldConfig": {"defaults": {"custom": {"align": "auto", "filterable": True}}, "overrides": overrides},
        "options": {"showHeader": True, "cellHeight": "sm",
                    "sortBy": [{"displayName": sort, "desc": True}] if sort else []},
    }


def variable(name: str, label: str, sql: str, multi: bool = True) -> dict:
    return {"name": name, "label": label, "type": "query", "datasource": DS, "query": sql,
            "refresh": 1, "multi": multi, "includeAll": multi, "current": {}, "sort": 1,
            "definition": sql}


def dashboard(uid: str, title: str, description: str, panels: list, time_from: str, time_to: str,
              variables: list | None = None, refresh: str = "") -> dict:
    return {
        "uid": uid, "title": title, "description": description, "tags": ["smart-campus"],
        "timezone": "Europe/London", "schemaVersion": 39, "version": 1, "editable": False,
        "graphTooltip": 1, "refresh": refresh, "time": {"from": time_from, "to": time_to},
        "templating": {"list": variables or []}, "panels": panels,
        "links": [{"title": "Smart Campus dashboards", "type": "dashboards", "tags": ["smart-campus"],
                   "asDropdown": False}],
    }


# ----------------------------------------------------------------------------- dashboards

BUILDING_FILTER = "building_id in (${building:sqlstring})"


def energy_dashboard() -> dict:
    lay = Layout()
    p = [text_panel(lay, (
        "**Campus energy - where it goes and what we could save.** Figures for the last full year (2016) "
        "use the university tariff and UK carbon factors (editable reference data). Readings flagged as "
        "faulty meters are excluded. Use *Building* above to focus on specific buildings."), h=2)]
    kpi = "marts.mart_building_energy_kpis"
    ooh = "marts.mart_out_of_hours_opportunity"
    p += [
        stat(lay, "Energy cost (2016)", f"select sum(cost_gbp) from {kpi} where {BUILDING_FILTER}",
             "currencyGBP", "Electricity + gas cost for the selected buildings, at contract rates.", decimals=2),
        stat(lay, "Carbon (2016)", f"select sum(tco2e) from {kpi} where {BUILDING_FILTER}",
             "suffix: tCO₂e", "Scope 1 (gas) + scope 2 (grid electricity) emissions."),
        stat(lay, "Out-of-hours saving opportunity", f"select sum(savings_gbp) from {ooh} where {BUILDING_FILTER}",
             "currencyGBP", "Annual saving if each building used energy out of hours like a typical "
             "building of the same type. See the table below for where."),
        stat(lay, "Carbon saving opportunity", f"select sum(savings_tco2e) from {ooh} where {BUILDING_FILTER}",
             "suffix: tCO₂e", "Annual carbon avoided by the out-of-hours opportunity."),
    ]
    lay.new_row()
    p.append(timeseries(
        lay, "Daily consumption by meter",
        # A day is only plotted if >= 80% of the usual number of meters reported valid data,
        # so a site-wide data outage shows as a gap rather than as a misleading drop to zero.
        f"""select local_date::timestamptz as time, meter as metric,
                   case when count(kwh) >= 0.8 * max(count(kwh)) over (partition by meter)
                        then sum(kwh) end as value
            from marts.fct_energy_daily where $__timeFilter(local_date) and {BUILDING_FILTER}
            group by 1, 2 order by 1""",
        "kwatth", "Total kWh per day. Gas rises in winter (heating); electricity dips in vacations. "
        "Gaps are days when fewer than 80% of meters reported valid data (source data outages).",
        ["electricity", "gas"], w=16))
    p.append(barchart(
        lay, "Typical electricity profile (avg kWh by hour)",
        f"""select lpad(local_hour::text, 2, '0') || ':00' as hour,
                   avg(kwh) filter (where not is_weekend) as weekday,
                   avg(kwh) filter (where is_weekend) as weekend
            from (select local_date, local_hour, is_weekend, sum(kwh_clean) as kwh
                  from marts.fct_energy_hourly
                  where meter = 'electricity' and $__timeFilter(reading_ts) and {BUILDING_FILTER}
                  group by 1, 2, 3) h
            group by local_hour order by local_hour""",
        "kwatth", "Average campus load at each hour of the day. A high overnight / weekend line is "
        "energy used while buildings are empty.", ["weekday", "weekend"], w=8))
    lay.new_row()
    p.append(table(
        lay, "Top out-of-hours saving opportunities",
        f"""select building_name as "Building", meter as "Meter",
                   unoccupied_share as "Out-of-hours share", peer_median_unoccupied_share as "Typical",
                   savings_gbp as "Saving £/yr", savings_tco2e as "tCO₂e/yr"
            from {ooh} where savings_kwh > 0 and {BUILDING_FILTER} order by savings_gbp desc limit 25""",
        "Buildings using a larger share of their energy outside occupied hours than similar buildings "
        "(same space use). Typical actions: BMS time-schedule review, heating set-back, plug-load and "
        "lighting controls.",
        {"Meter": {"width": 95},
         "Out-of-hours share": {"unit": "percentunit", "bar": True, "width": 170},
         "Typical": {"unit": "percentunit", "decimals": 0},
         "Saving £/yr": {"unit": "currencyGBP", "decimals": 0},
         "tCO₂e/yr": {"decimals": 1}}, w=14, h=11))
    p.append(table(
        lay, "Energy intensity vs similar buildings (electricity)",
        f"""select building_name as "Building", space_usage as "Use", eui_kwh_m2 as "kWh/m²",
                   eui_vs_peer_median as "vs typical"
            from {kpi} where meter = 'electricity' and eui_kwh_m2 is not null and {BUILDING_FILTER}
            order by eui_vs_peer_median desc""",
        "Electricity use per m² of floor area compared with the median building of the same use. "
        "+50% means half as much again as a typical building. Very high values can also indicate a "
        "sub-meter serving more than one building - check with the metering contractor.",
        {"kWh/m²": {"decimals": 0}, "vs typical": {"unit": "percentunit", "decimals": 0}}, w=10, h=11))
    building_var = variable("building", "Building",
                            "select building_id as __value, building_name || ' (' || space_usage || ')' as __text "
                            "from marts.dim_building order by 2")
    return dashboard("campus-energy", "Campus Energy Overview",
                     "Energy consumption, cost, carbon and savings opportunities by building.",
                     p, "2016-01-01T00:00:00Z", "2017-12-31T23:59:59Z", [building_var])


def estates_dashboard() -> dict:
    lay = Layout()
    p = [text_panel(lay, (
        "**Estate condition, maintenance and accessibility.** Condition grades follow the HE sector "
        "A (as new) - D (life expired) scale. *Backlog* is the cost to bring C/D assets back to sound "
        "condition. Note: estates data in this demo is synthetic (see project README)."), h=2)]
    ac, sc = "marts.mart_asset_condition", "marts.mart_building_scorecard"
    p += [
        stat(lay, "Maintenance backlog", f"select sum(backlog_cost_gbp) from {ac}", "currencyGBP",
             "Estimated cost to remedy assets in poor (C) or life-expired (D) condition, plus minor B repairs."),
        stat(lay, "Assets to replace now", f"select count(*) from {ac} where investment_priority like '1%'",
             "short", "Critical assets in poor condition - highest risk of failure affecting teaching/research.",
             thresholds=[{"color": NEUTRAL, "value": None}]),
        stat(lay, "Reactive share of maintenance (2017)",
             """select sum(total_cost_gbp) filter (where work_type = 'reactive') / sum(total_cost_gbp)
                from marts.fct_work_orders where extract(year from raised_date) = 2017""",
             "percentunit", "Share of spend on breakdowns rather than planned work. Sector good practice "
             "targets roughly 30% reactive / 70% planned.", decimals=0,
             thresholds=[{"color": STATUS["good"], "value": None}, {"color": STATUS["warning"], "value": 0.4},
                         {"color": STATUS["critical"], "value": 0.6}]),
        stat(lay, "Buildings rated Red for access",
             "select count(*) from marts.mart_accessibility_compliance where rag_status = 'Red'", "short",
             "Buildings with a failed high-severity accessibility check or < 60% compliance."),
    ]
    lay.new_row()
    p.append(timeseries(
        lay, "Monthly maintenance spend - reactive vs planned",
        """select month_start::timestamptz as time, 'reactive' as metric, sum(reactive_cost_gbp) as value
             from marts.mart_maintenance_spend_monthly where $__timeFilter(month_start) group by 1
           union all
           select month_start::timestamptz, 'planned', sum(planned_cost_gbp)
             from marts.mart_maintenance_spend_monthly where $__timeFilter(month_start) group by 1
           order by 1""",
        "currencyGBP", "Total cost of completed and open work orders raised each month.",
        ["reactive", "planned"], w=14, bars=True, stacked=True))
    p.append(barchart(
        lay, "Backlog by asset type",
        f"""select asset_class as "Asset type", sum(backlog_cost_gbp) as "Backlog"
            from {ac} group by 1 order by 2 desc""",
        "currencyGBP", "Where the backlog sits - informs the capital programme.", [], w=10, horizontal=True))
    lay.new_row()
    p.append(table(
        lay, "Investment priority list",
        f"""select building_name as "Building", asset_class as "Asset", condition_grade as "Grade",
                   age_years as "Age", remaining_life_years as "Life left (yrs)",
                   reactive_jobs_ref_year as "Repairs 2017", replacement_cost_gbp as "Replacement £",
                   investment_priority as "Priority"
            from {ac} where investment_priority like '1%' or investment_priority like '2%'
            order by risk_score desc, replacement_cost_gbp desc limit 100""",
        "Assets ranked by risk = condition x criticality (+ frequent breakdowns). Use for the capital plan.",
        {"Replacement £": {"unit": "currencyGBP", "decimals": 0}}, w=24, h=10))
    lay.new_row()
    p.append(table(
        lay, "Accessibility compliance by building",
        """select building_name as "Building", space_usage as "Use", age_band as "Built",
                  compliance_pct as "Compliance", high_severity_fails as "High-severity fails",
                  remediation_cost_gbp as "Remediation £", rag_status as "Status"
           from marts.mart_accessibility_compliance order by compliance_pct""",
        "Latest audit against BS 8300 / Approved Document M style checks.",
        {"Compliance": {"unit": "percentunit", "bar": True},
         "Remediation £": {"unit": "currencyGBP", "decimals": 0}, "Status": {"rag": True}}, w=13, h=11))
    p.append(table(
        lay, "High-severity accessibility gaps",
        """select building_name as "Building", check_name as "Check", result as "Result",
                  remediation_cost_gbp as "Est. cost"
           from marts.mart_accessibility_gaps where severity = 'high' order by remediation_cost_gbp desc""",
        "Failed or partially met high-severity checks - the remediation worklist.",
        {"Est. cost": {"unit": "currencyGBP", "decimals": 0}}, w=11, h=11))
    lay.new_row()
    p.append(table(
        lay, "Building scorecard",
        f"""select building_name as "Building", space_usage as "Use", floor_area_m2 as "m²",
                   energy_cost_gbp as "Energy £ (2016)", ooh_savings_gbp as "Energy saving £",
                   backlog_cost_gbp as "Backlog £", maintenance_gbp_per_m2 as "Maint. £/m² (2017)",
                   reactive_cost_share as "Reactive share", accessibility_compliance_pct as "Access",
                   accessibility_rag as "Access status"
            from {sc} order by backlog_cost_gbp desc nulls last""",
        "One row per building across energy, condition, maintenance and accessibility.",
        {"m²": {"decimals": 0}, "Energy £ (2016)": {"unit": "currencyGBP", "decimals": 0},
         "Energy saving £": {"unit": "currencyGBP", "decimals": 0},
         "Backlog £": {"unit": "currencyGBP", "decimals": 0},
         "Maint. £/m² (2017)": {"unit": "currencyGBP", "decimals": 2},
         "Reactive share": {"unit": "percentunit", "decimals": 0},
         "Access": {"unit": "percentunit", "decimals": 0}, "Access status": {"rag": True}}, w=24, h=12))
    return dashboard("campus-estates", "Estates: Assets, Maintenance & Accessibility",
                     "Asset condition and backlog, maintenance spend and accessibility compliance.",
                     p, "2016-01-01T00:00:00Z", "2017-12-31T23:59:59Z")


def platform_dashboard() -> dict:
    lay = Layout()
    p = [text_panel(lay, (
        "**Platform health - for the data engineer and BMS team.** Live view of the telemetry pipeline "
        "(wall-clock time): ingestion throughput, contract violations sent to the dead-letter table, and "
        "meter data quality."), h=2)]
    p += [
        stat(lay, "Readings ingested (last 15 min)",
             "select coalesce(sum(readings), 0) from raw.v_ingest_health where minute > now() - interval '15 minutes'",
             "short", "Streamed readings written to the raw layer in the last 15 minutes."),
        stat(lay, "Rejected messages (24 h)", "select coalesce(sum(rejects), 0) from raw.v_reject_summary",
             "short", "Messages that broke the telemetry contract and were dead-lettered.",
             thresholds=[{"color": STATUS["good"], "value": None}, {"color": STATUS["warning"], "value": 1},
                         {"color": STATUS["critical"], "value": 500}]),
        stat(lay, "Meters below 95% completeness",
             "select count(*) from marts.mart_data_quality where completeness < 0.95", "short",
             "Building meters with more than 5% of hourly readings missing (as of the last dbt run)."),
        stat(lay, "Readings flagged as faulty",
             "select sum(spike_readings + stuck_readings)::float / sum(readings) from marts.mart_data_quality",
             "percentunit", "Share of readings excluded from reporting as spikes or stuck sensors.", decimals=1),
    ]
    lay.new_row()
    p.append(timeseries(
        lay, "Ingestion throughput (readings / minute)",
        """select minute as time, source as metric, sum(readings) as value from raw.v_ingest_health
           where $__timeFilter(minute) group by 1, 2 order by 1""",
        "short", "Readings upserted per minute by the ingest Lambda.", [], w=14))
    p[-1]["fieldConfig"]["defaults"]["color"] = {"mode": "fixed", "fixedColor": SERIES["electricity"]}
    p.append(barchart(
        lay, "Rejections by reason (24 h)",
        """select split_part(reason, ':', 1) as "Reason", sum(rejects) as "Rejected"
           from raw.v_reject_summary group by 1 order by 2 desc""",
        "short", "Why messages were dead-lettered. Persistent reasons point to a device or integration fault.",
        [], w=10, horizontal=True))
    lay.new_row()
    p.append(table(
        lay, "Latest anomalies from the live stream",
        """select reading_ts as "Reading time", building_name || ' · ' || meter as "Meter",
                  anomaly_type as "Type", observed_kwh as "Observed kWh", expected_kwh as "Expected kWh"
           from marts.mart_energy_anomalies where load_source = 'stream'
           order by reading_ts desc limit 100""",
        "Spikes and stuck sensors detected in streamed telemetry - triage with the BMS contractor.",
        {"Observed kWh": {"decimals": 2}, "Expected kWh": {"decimals": 2}}, w=14, h=10))
    p.append(table(
        lay, "Meters with the lowest completeness",
        """select building_name || ' · ' || meter as "Meter", completeness as "Completeness",
                  stuck_readings as "Flat-line hrs"
           from marts.mart_data_quality order by completeness limit 50""",
        "Data completeness per meter across the full history.",
        {"Completeness": {"unit": "percentunit", "bar": True}}, w=10, h=10))
    return dashboard("platform-health", "Platform Health & Live Telemetry",
                     "Pipeline throughput, dead-lettered messages and telemetry data quality.",
                     p, "now-3h", "now", refresh="30s")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for d in (energy_dashboard(), estates_dashboard(), platform_dashboard()):
        path = OUT / f"{d['uid']}.json"
        path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(d['panels'])} panels)")


if __name__ == "__main__":
    main()
