"""Batch loaders into the raw schema.

    python -m smart_campus.load reference                  # building metadata + weather
    python -m smart_campus.load backfill --until 2017-07-01 # historical meter readings
    python -m smart_campus.load synthetic                  # estates data (see synthetic.py)

The historical backfill deliberately bypasses MQTT: pushing 3.5M historical readings
through the device path would be slow and is not how a real migration from a legacy
BMS historian works. It uses COPY + INSERT ... ON CONFLICT DO NOTHING, so it is safe
to re-run and never overwrites readings that arrived via the stream.
See docs/adr/0002-backfill-vs-stream.md.
"""

from __future__ import annotations

import argparse
import io
import logging
import time

import pandas as pd
import psycopg

from smart_campus.config import SITE_ID, SYNTHETIC_DIR, db_conninfo, site_dir

log = logging.getLogger("load")

CHUNK_ROWS = 250_000


def copy_frame(cur: psycopg.Cursor, table: str, df: pd.DataFrame) -> None:
    columns = ", ".join(df.columns)
    with cur.copy(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv)") as copy:
        for start in range(0, len(df), CHUNK_ROWS):
            buf = io.StringIO()
            df.iloc[start : start + CHUNK_ROWS].to_csv(buf, index=False, header=False)
            copy.write(buf.getvalue())


def replace_table(conn: psycopg.Connection, table: str, df: pd.DataFrame) -> None:
    """Full refresh for small reference tables (one transaction: readers never see it empty)."""
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table}")
        copy_frame(cur, table, df)
    log.info("%s: %d rows", table, len(df))


def load_reference(conn: psycopg.Connection, site_id: str) -> None:
    replace_table(conn, "raw.building_metadata", pd.read_parquet(site_dir(site_id) / "buildings.parquet"))
    replace_table(conn, "raw.weather_hourly", pd.read_parquet(site_dir(site_id) / "weather.parquet"))


def backfill(conn: psycopg.Connection, site_id: str, until: str) -> None:
    readings = pd.read_parquet(site_dir(site_id) / "readings.parquet")
    readings = readings[readings["ts"] < pd.Timestamp(until, tz="UTC")]
    readings.insert(0, "site_id", site_id)
    started = time.monotonic()
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE backfill_stage "
            "(site_id text, building_id text, meter text, ts timestamptz, value double precision) "
            "ON COMMIT DROP"
        )
        copy_frame(cur, "backfill_stage", readings[["site_id", "building_id", "meter", "ts", "value"]])
        cur.execute(
            """
            INSERT INTO raw.meter_reading (site_id, building_id, meter, ts, value, source)
            SELECT site_id, building_id, meter, ts, value, 'backfill' FROM backfill_stage
            ON CONFLICT (building_id, meter, ts) DO NOTHING
            """
        )
        inserted = cur.rowcount
    log.info("backfill: %d staged, %d new rows in %.0fs", len(readings), inserted, time.monotonic() - started)


SYNTHETIC_TABLES = ("asset_register", "condition_survey", "work_order", "accessibility_audit")


def load_synthetic(conn: psycopg.Connection) -> None:
    for name in SYNTHETIC_TABLES:
        replace_table(conn, f"raw.{name}", pd.read_parquet(SYNTHETIC_DIR / f"{name}.parquet"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch loaders into the raw schema")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reference")
    bf = sub.add_parser("backfill")
    bf.add_argument("--until", default="2017-07-01", help="load readings strictly before this date")
    sub.add_parser("synthetic")
    parser.add_argument("--site", default=SITE_ID)
    args = parser.parse_args()

    with psycopg.connect(db_conninfo()) as conn:
        if args.command == "reference":
            load_reference(conn, args.site)
        elif args.command == "backfill":
            backfill(conn, args.site, args.until)
        else:
            load_synthetic(conn)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
