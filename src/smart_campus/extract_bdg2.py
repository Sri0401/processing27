"""Extract one site from the Building Data Genome Project 2 (BDG2) dataset.

BDG2 ships meter data "wide" (one column per building, ~1,600 columns). This step
selects a single campus, reshapes to a tidy long format and writes Parquet:

    data/staged/<site>/buildings.parquet
    data/staged/<site>/readings.parquet    building_id, meter, ts, value
    data/staged/<site>/weather.parquet

Timestamps: BDG2 is hourly local *standard* time with no DST shifts (17,544 rows
= 731 days x 24). For UK sites standard time is GMT, so we tag them as UTC.
See docs/adr/0003-timestamps-and-units.md.

Usage:  python -m smart_campus.extract_bdg2 [--site Lamb]
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from smart_campus.config import METERS, RAW_DIR, SITE_ID, site_dir

log = logging.getLogger(__name__)

METADATA_COLUMNS = {
    "building_id": "building_id",
    "site_id": "site_id",
    "primaryspaceusage": "primary_space_usage",
    "sub_primaryspaceusage": "sub_primary_space_usage",
    "sqm": "sqm",
    "lat": "lat",
    "lng": "lng",
    "timezone": "timezone",
    "yearbuilt": "year_built",
    "numberoffloors": "number_of_floors",
    "occupants": "occupants",
    "electricity": "has_electricity_meter",
    "gas": "has_gas_meter",
}

WEATHER_COLUMNS = {
    "timestamp": "ts",
    "site_id": "site_id",
    "airTemperature": "air_temperature",
    "dewTemperature": "dew_temperature",
    "cloudCoverage": "cloud_coverage",
    "precipDepth1HR": "precip_depth_1hr",
    "seaLvlPressure": "sea_lvl_pressure",
    "windSpeed": "wind_speed",
}


def extract_buildings(site_id: str) -> pd.DataFrame:
    meta = pd.read_csv(RAW_DIR / "metadata.csv", usecols=list(METADATA_COLUMNS))
    meta = meta[meta["site_id"] == site_id].rename(columns=METADATA_COLUMNS)
    for col in ("has_electricity_meter", "has_gas_meter"):
        meta[col] = meta[col].eq("Yes")
    for col in ("year_built", "number_of_floors", "occupants"):
        meta[col] = meta[col].astype("Int64")
    return meta.reset_index(drop=True)


def extract_readings(site_id: str, meter: str) -> pd.DataFrame:
    path = RAW_DIR / f"{meter}_cleaned.csv"
    header = pd.read_csv(path, nrows=0).columns
    columns = ["timestamp", *[c for c in header if c.startswith(f"{site_id}_")]]
    wide = pd.read_csv(path, usecols=columns, parse_dates=["timestamp"])
    long = wide.melt(id_vars="timestamp", var_name="building_id", value_name="value")
    long = long.dropna(subset=["value"])
    long["meter"] = meter
    long["ts"] = long.pop("timestamp").dt.tz_localize("UTC")
    log.info("%s: %d buildings, %d readings", meter, len(columns) - 1, len(long))
    return long[["building_id", "meter", "ts", "value"]]


def extract_weather(site_id: str) -> pd.DataFrame:
    weather = pd.read_csv(RAW_DIR / "weather.csv", usecols=list(WEATHER_COLUMNS))
    weather = weather[weather["site_id"] == site_id].rename(columns=WEATHER_COLUMNS)
    weather["ts"] = pd.to_datetime(weather["ts"]).dt.tz_localize("UTC")
    return weather.drop_duplicates(subset=["site_id", "ts"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", default=SITE_ID)
    args = parser.parse_args()

    out = site_dir(args.site)
    out.mkdir(parents=True, exist_ok=True)

    buildings = extract_buildings(args.site)
    if buildings.empty:
        raise SystemExit(f"Site {args.site!r} not found in BDG2 metadata")
    buildings.to_parquet(out / "buildings.parquet", index=False)

    readings = pd.concat([extract_readings(args.site, m) for m in METERS], ignore_index=True)
    readings.to_parquet(out / "readings.parquet", index=False)

    extract_weather(args.site).to_parquet(out / "weather.parquet", index=False)
    log.info("Wrote %d buildings, %d readings to %s", len(buildings), len(readings), out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
