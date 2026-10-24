"""Replay BDG2 meter readings as live building-system telemetry over MQTT.

Every simulated hour, each meter on the campus publishes one reading to
campus/{site}/{building}/{meter}/telemetry. A small, configurable share of readings
is corrupted to exercise the platform's data-quality defences:

    spike      value x 15                   -> accepted, flagged by dbt anomaly tests
    stuck      meter repeats a value for 30h -> accepted, flagged by dbt flat-line check
    negative   value < 0                    -> rejected by the ingest contract
    malformed  missing field / bad JSON     -> rejected by the ingest contract
    duplicate  same message sent twice      -> absorbed by idempotent upsert

Usage:  python -m smart_campus.simulator [--start 2017-07-01] [--hours 48] [--speedup 1]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
import uuid

import paho.mqtt.client as mqtt
import pandas as pd

from smart_campus.config import SITE_ID, site_dir

log = logging.getLogger("simulator")

FAULTS = ("spike", "stuck", "negative", "malformed", "duplicate")
STUCK_HOURS = 30  # longer than the dbt `stuck_hours` threshold (24) so it is detected


def topic(site_id: str, building_id: str, meter: str) -> str:
    return f"campus/{site_id}/{building_id}/{meter}/telemetry"


def build_message(site_id: str, building_id: str, meter: str, ts: pd.Timestamp, value: float) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "site_id": site_id,
        "building_id": building_id,
        "meter": meter,
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "value": round(float(value), 4),
        "unit": "kWh",
        "schema_version": 1,
    }


class FaultInjector:
    def __init__(self, rate: float, seed: int = 42):
        self.rate = rate
        self.rng = random.Random(seed)
        self.stuck: dict[tuple[str, str], tuple[float, int]] = {}  # key -> (value, hours left)
        self.counts = dict.fromkeys(FAULTS, 0)

    def apply(self, msg: dict) -> list[bytes]:
        """Return the payload(s) to publish for one reading."""
        key = (msg["building_id"], msg["meter"])
        if key in self.stuck:
            value, left = self.stuck[key]
            msg["value"] = value
            self.stuck[key] = (value, left - 1)
            if left <= 1:
                del self.stuck[key]
            return [json.dumps(msg).encode()]

        if self.rng.random() >= self.rate:
            return [json.dumps(msg).encode()]

        fault = self.rng.choice(FAULTS)
        self.counts[fault] += 1
        if fault == "spike":
            msg["value"] = round(msg["value"] * 15 + 50, 4)
        elif fault == "stuck":
            self.stuck[key] = (msg["value"], STUCK_HOURS)
        elif fault == "negative":
            msg["value"] = -abs(msg["value"]) - 1
        elif fault == "malformed":
            if self.rng.random() < 0.5:
                return [b'{"message_id": "' + msg["message_id"].encode() + b'", "value": ']
            msg.pop("ts")
        elif fault == "duplicate":
            payload = json.dumps(msg).encode()
            return [payload, payload]
        return [json.dumps(msg).encode()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", default=SITE_ID)
    parser.add_argument("--start", default=os.getenv("SIM_START", "2017-07-01T00:00:00Z"))
    parser.add_argument("--hours", type=int, default=None, help="stop after N simulated hours")
    parser.add_argument("--speedup", type=float, default=float(os.getenv("SIM_SPEEDUP", "1")),
                        help="simulated hours per real second")
    parser.add_argument("--fault-rate", type=float, default=float(os.getenv("SIM_FAULT_RATE", "0.002")))
    args = parser.parse_args()

    readings = pd.read_parquet(site_dir(args.site) / "readings.parquet")
    readings = readings[readings["ts"] >= pd.Timestamp(args.start)].sort_values("ts")
    hours = readings.groupby("ts", sort=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"simulator-{args.site}")
    client.connect(os.getenv("MQTT_HOST", "localhost"), int(os.getenv("MQTT_PORT", "1883")))
    client.loop_start()

    faults = FaultInjector(args.fault_rate)
    interval = 1.0 / args.speedup
    info = None
    for i, (ts, frame) in enumerate(hours):
        if args.hours is not None and i >= args.hours:
            break
        tick = time.monotonic()
        for row in frame.itertuples(index=False):
            msg = build_message(args.site, row.building_id, row.meter, ts, row.value)
            for payload in faults.apply(msg):
                info = client.publish(topic(args.site, row.building_id, row.meter), payload, qos=1)
        log.info("published %s (%d readings) faults=%s", ts, len(frame), faults.counts)
        time.sleep(max(0.0, interval - (time.monotonic() - tick)))

    if info is not None:
        info.wait_for_publish(timeout=30)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
