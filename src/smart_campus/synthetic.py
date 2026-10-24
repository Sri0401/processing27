"""Generate synthetic estates data for the BDG2 campus (no open dataset exists for these).

Outputs (data/synthetic/*.parquet), all keyed to the real BDG2 building ids:
    asset_register       major building services / fabric components per building
    condition_survey     annual condition grade per asset (HE-sector A-D scale)
    work_order           CAFM-style reactive + planned maintenance jobs with costs
    accessibility_audit  BS 8300 / Approved Document M style checks per building

The data is deterministic (fixed seed) and causally plausible rather than random:
older assets get worse grades, worse grades fail more often and cost more to fix, and
buildings designed after the 2004 accessibility duties pass more audit checks.
Assumptions are documented in docs/data-sources.md.

Usage:  python -m smart_campus.synthetic
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from smart_campus.config import SITE_ID, SYNTHETIC_DIR, site_dir

log = logging.getLogger("synthetic")

SEED = 7
SURVEY_YEARS = (2016, 2017)
WORK_START, WORK_END = pd.Timestamp("2016-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC")


@dataclass(frozen=True)
class AssetClass:
    name: str
    expected_life: int        # years (CIBSE Guide M style indicative lives)
    cost_per_sqm: float       # replacement cost, GBP per m2 of floor area
    criticality: int          # 1 critical .. 3 low
    reactive_rate: float      # reactive jobs / asset / year when in good condition
    ppm_per_year: int         # planned preventive maintenance visits per year
    faults: tuple[str, ...]


ASSET_CLASSES = (
    AssetClass("Boiler", 20, 22, 1, 1.5, 2, ("No heating", "Boiler lockout", "Low pressure", "Burner fault")),
    AssetClass("Air Handling Unit", 20, 30, 2, 1.2, 2, ("Fan belt failure", "Filter blocked", "Damper stuck")),
    AssetClass("Chiller", 20, 35, 2, 1.0, 4, ("High pressure trip", "Refrigerant leak", "Compressor fault")),
    AssetClass("Lift", 25, 0, 1, 2.0, 12, ("Lift out of service", "Door fault", "Entrapment")),
    AssetClass("Roof", 30, 55, 2, 0.6, 1, ("Roof leak", "Gutter blocked", "Membrane damage")),
    AssetClass("Windows", 35, 40, 3, 0.8, 0, ("Window will not close", "Broken pane", "Seal failure")),
    AssetClass("Lighting", 15, 18, 3, 2.5, 1, ("Lights out", "Emergency light fault", "Sensor fault")),
    AssetClass("Fire Alarm", 15, 9, 1, 0.8, 4, ("False alarm", "Detector fault", "Panel fault")),
    AssetClass("Electrical Distribution", 30, 28, 1, 0.5, 1, ("Breaker tripping", "Socket fault", "RCD fault")),
)
LIFT_COST_GBP = 85_000

PRIORITIES = {  # share of reactive jobs, mean hours to complete, cost multiplier
    "P1": (0.10, 6, 1.8),
    "P2": (0.25, 30, 1.3),
    "P3": (0.45, 120, 1.0),
    "P4": (0.20, 480, 0.8),
}
CONTRACTORS = ("In-house Estates", "Mechanical Services Ltd", "Electrical Contracts Co", "Lift Specialists Ltd",
               "Fabric & Roofing Ltd", "Fire Systems Ltd")
CLASS_CONTRACTOR = {"Boiler": 1, "Air Handling Unit": 1, "Chiller": 1, "Lift": 3, "Roof": 4, "Windows": 4,
                    "Lighting": 2, "Fire Alarm": 5, "Electrical Distribution": 2}

ACCESS_CHECKS = (  # code, name, severity, remediation cost GBP, applies-to rule
    ("AX01", "Step-free principal entrance", "high", 18_000, "all"),
    ("AX02", "Accessible WC available", "high", 25_000, "all"),
    ("AX03", "Lift access to all public floors", "high", 120_000, "multi_storey"),
    ("AX04", "Hearing enhancement system at reception", "medium", 1_500, "all"),
    ("AX05", "Designated accessible parking within 50 m", "medium", 4_000, "all"),
    ("AX06", "Tactile and high-contrast wayfinding", "low", 6_000, "all"),
    ("AX07", "Powered or automatic entrance doors", "medium", 9_000, "all"),
    ("AX08", "Refuge areas with two-way communication", "high", 7_500, "multi_storey"),
    ("AX09", "Changing Places toilet", "medium", 45_000, "large"),
    ("AX10", "Visual fire alarm beacons", "high", 5_000, "all"),
)


def grade_from_age(age_ratio: np.ndarray) -> np.ndarray:
    """HE-sector condition scale: A as new, B sound, C operational but major repair due, D life expired."""
    return np.select([age_ratio < 0.3, age_ratio < 0.7, age_ratio < 1.05], ["A", "B", "C"], "D")


def build_assets(buildings: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for b in buildings.itertuples(index=False):
        floors = int(b.number_of_floors or 1)
        for ac in ASSET_CLASSES:
            if ac.name == "Boiler" and not b.has_gas_meter:
                continue
            if ac.name == "Chiller" and (b.sqm < 3000 or b.primary_space_usage not in ("Education", "Office")):
                continue
            count = max(1, floors // 4) if ac.name == "Lift" else 1
            if ac.name == "Lift" and floors < 2:
                continue
            for n in range(count):
                # walk forward through replacement cycles to find the current install year
                install = int(b.year_built)
                while install + ac.expected_life * rng.uniform(0.95, 1.4) < 2016:
                    install += int(ac.expected_life * rng.uniform(0.95, 1.4))
                footprint = b.sqm / floors if ac.name == "Roof" else b.sqm
                cost = LIFT_COST_GBP if ac.name == "Lift" else ac.cost_per_sqm * footprint
                rows.append({
                    "asset_id": f"{b.building_id}-{ac.name.replace(' ', '')[:6].upper()}-{n + 1:02d}",
                    "building_id": b.building_id,
                    "asset_class": ac.name,
                    "description": f"{ac.name} {n + 1}" if count > 1 else ac.name,
                    "install_year": install,
                    "expected_life_years": ac.expected_life,
                    "replacement_cost_gbp": round(cost * rng.uniform(0.85, 1.2), 2),
                    "criticality": ac.criticality,
                })
    return pd.DataFrame(rows)


def build_surveys(assets: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    frames = []
    for year in SURVEY_YEARS:
        age_ratio = (year - assets["install_year"]) / assets["expected_life_years"]
        noisy = age_ratio + rng.normal(0, 0.12, len(assets))
        dates = pd.to_datetime(f"{year}-03-01") + pd.to_timedelta(rng.integers(0, 60, len(assets)), unit="D")
        frames.append(pd.DataFrame({
            "survey_id": [f"CS{year}-{i:05d}" for i in range(len(assets))],
            "asset_id": assets["asset_id"],
            "survey_date": dates.date,
            "condition_grade": grade_from_age(noisy.to_numpy()),
            "surveyor": rng.choice(["Estates Surveying Team", "External Surveyor"], len(assets)),
            "notes": None,
        }))
    surveys = pd.concat(frames, ignore_index=True)
    # Grades never improve between surveys unless the asset was replaced (it was not, in 2016-17)
    surveys = surveys.sort_values(["asset_id", "survey_date"])
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    worst = surveys["condition_grade"].map(order).groupby(surveys["asset_id"]).cummax()
    surveys["condition_grade"] = worst.map({v: k for k, v in order.items()})
    return surveys.sort_values("survey_id").reset_index(drop=True)


def build_work_orders(assets: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    by_class = {ac.name: ac for ac in ASSET_CLASSES}
    span_hours = (WORK_END - WORK_START).total_seconds() / 3600
    years = span_hours / 8760
    priorities = list(PRIORITIES)
    p_weights = [v[0] for v in PRIORITIES.values()]
    rows = []
    for a in assets.itertuples(index=False):
        ac = by_class[a.asset_class]
        age_ratio = (2017 - a.install_year) / a.expected_life_years
        # failure rate climbs steeply once an asset passes ~60% of its life
        rate = ac.reactive_rate * (1 + 4 * max(0.0, age_ratio - 0.6) ** 1.5)
        n_reactive = rng.poisson(rate * years)
        n_planned = ac.ppm_per_year * 2
        planned_times = WORK_START + pd.to_timedelta(
            (np.arange(n_planned) + rng.uniform(0, 1, n_planned)) * span_hours / max(n_planned, 1), unit="h")
        reactive_times = WORK_START + pd.to_timedelta(rng.uniform(0, span_hours, n_reactive), unit="h")
        base_cost = 120 + a.replacement_cost_gbp ** 0.5 * 2.5
        for kind, times in (("reactive", reactive_times), ("planned", planned_times)):
            for raised in times:
                if kind == "reactive":
                    priority = rng.choice(priorities, p=p_weights)
                    description = rng.choice(ac.faults)
                else:
                    priority, description = "P4", f"PPM service - {ac.name}"
                _, mean_hours, cost_mult = PRIORITIES[priority]
                completed = raised + pd.Timedelta(hours=float(rng.gamma(2.0, mean_hours / 2)))
                labour = base_cost * cost_mult * rng.lognormal(0, 0.45) * (0.6 if kind == "planned" else 1)
                material = labour * rng.uniform(0.1, 1.5) if kind == "reactive" else labour * rng.uniform(0, 0.3)
                is_open = completed >= WORK_END
                rows.append({
                    "asset_id": a.asset_id,
                    "building_id": a.building_id,
                    "raised_at": raised.floor("min"),
                    "completed_at": pd.NaT if is_open else completed.floor("min"),
                    "work_type": kind,
                    "priority": priority,
                    "category": ac.name,
                    "description": description,
                    "labour_cost_gbp": round(labour, 2),
                    "material_cost_gbp": round(material, 2),
                    "contractor": CONTRACTORS[0 if rng.random() < 0.35 else CLASS_CONTRACTOR[ac.name]],
                    "status": "open" if is_open else "completed",
                })
    orders = pd.DataFrame(rows).sort_values("raised_at").reset_index(drop=True)
    orders.insert(0, "work_order_id", [f"WO{i:06d}" for i in range(1, len(orders) + 1)])
    return orders


def build_accessibility(buildings: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for b in buildings.itertuples(index=False):
        floors = int(b.number_of_floors or 1)
        audit_date = (pd.Timestamp("2017-01-09") + pd.Timedelta(days=int(rng.integers(0, 300)))).date()
        # Designed after the 2004 DDA duties -> mostly compliant; older stock depends on refurbishment
        base = 0.97 if b.year_built >= 2004 else (0.88 if b.year_built >= 1970 else 0.8)
        base = min(base + rng.normal(0, 0.06), 0.99)  # some buildings have been refurbished
        for code, name, severity, cost, rule in ACCESS_CHECKS:
            applies = rule == "all" or (rule == "multi_storey" and floors > 1) or (rule == "large" and b.sqm > 5000)
            if not applies:
                result = "n/a"
            else:
                p_pass = base * (0.75 if code in ("AX09", "AX07") else 1.0)
                r = rng.random()
                result = "pass" if r < p_pass else ("partial" if r < p_pass + (1 - p_pass) * 0.4 else "fail")
            remediation = {"pass": 0, "n/a": 0, "partial": 0.4, "fail": 1.0}[result] * cost
            rows.append({
                "audit_id": f"AA-{b.building_id}-{code}",
                "building_id": b.building_id,
                "audit_date": audit_date,
                "check_code": code,
                "check_name": name,
                "result": result,
                "severity": severity,
                "remediation_cost_gbp": round(remediation * rng.uniform(0.8, 1.3), 2),
            })
    return pd.DataFrame(rows)


def main() -> None:
    rng = np.random.default_rng(SEED)
    buildings = pd.read_parquet(site_dir(SITE_ID) / "buildings.parquet")
    # A few BDG2 buildings lack year_built / floors: impute from buildings of the same use
    for col in ("year_built", "number_of_floors"):
        by_use = buildings.groupby("primary_space_usage")[col].transform("median")
        buildings[col] = buildings[col].fillna(by_use.round()).fillna(buildings[col].median()).astype(int)
    SYNTHETIC_DIR.mkdir(parents=True, exist_ok=True)

    assets = build_assets(buildings, rng)
    outputs = {
        "asset_register": assets,
        "condition_survey": build_surveys(assets, rng),
        "work_order": build_work_orders(assets, rng),
        "accessibility_audit": build_accessibility(buildings, rng),
    }
    for name, df in outputs.items():
        df.to_parquet(SYNTHETIC_DIR / f"{name}.parquet", index=False)
        log.info("%s: %d rows", name, len(df))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
