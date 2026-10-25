"""Replay BDG2 meter readings as live building-system telemetry.

Every simulated hour, each meter on the campus produces one reading. Two transports:

    MQTT (default)   one message per reading to campus/{site}/{building}/{meter}/telemetry
                     - local Mosquitto, or AWS IoT Core
    HTTPS gateway    --http-endpoint URL: one batched POST per simulated hour, the way a BMS
                     gateway forwards buffered data - Azure Container Apps ingest API

A small, configurable share of readings is corrupted to exercise the platform's
data-quality defences:

    spike      value x 15                   -> accepted, flagged by dbt anomaly tests
    stuck      meter repeats a value for 30h -> accepted, flagged by dbt flat-line check
    negative   value < 0                    -> rejected by the ingest contract
    malformed  missing field / bad JSON     -> rejected by the ingest contract
    duplicate  same message sent twice      -> absorbed by idempotent upsert

Usage:  python -m smart_campus.simulator [--start 2017-07-01] [--hours 48] [--speedup 1]
        python -m smart_campus.simulator --http-endpoint https://.../telemetry   (INGEST_API_KEY)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
import urllib.error
import urllib.request
import uuid

import paho.mqtt.client as mqtt
import pandas as pd

from smart_campus.config import SITE_ID, site_dir
from smart_campus.payloads import decode

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
    parser.add_argument("--http-endpoint", default=os.getenv("SIM_HTTP_ENDPOINT"),
                        help="POST hourly gateway batches here instead of publishing to MQTT")
    args = parser.parse_args()

    readings = pd.read_parquet(site_dir(args.site) / "readings.parquet")
    readings = readings[readings["ts"] >= pd.Timestamp(args.start)].sort_values("ts")
    hours = readings.groupby("ts", sort=True)

    publisher = (HttpGatewayPublisher(args.http_endpoint, os.environ["INGEST_API_KEY"]) if args.http_endpoint
                 else MqttPublisher(args.site))
    faults = FaultInjector(args.fault_rate)
    interval = 1.0 / args.speedup
    for i, (ts, frame) in enumerate(hours):
        if args.hours is not None and i >= args.hours:
            break
        tick = time.monotonic()
        batch = []
        for row in frame.itertuples(index=False):
            msg = build_message(args.site, row.building_id, row.meter, ts, row.value)
            batch += [(topic(args.site, row.building_id, row.meter), p) for p in faults.apply(msg)]
        publisher.publish(batch)
        log.info("published %s (%d readings) faults=%s", ts, len(frame), faults.counts)
        time.sleep(max(0.0, interval - (time.monotonic() - tick)))
    publisher.close()


class MqttPublisher:
    """One MQTT message per reading (QoS 1).

    Local Mosquitto: plain MQTT on 1883. AWS IoT Core: set MQTT_PORT=8883, MQTT_TLS_CERT and
    MQTT_TLS_KEY (the device's X.509 certificate) and MQTT_CLIENT_ID to the IoT Thing name -
    the IoT policy only lets a client connect under its own Thing name. MQTT_MAX_RATE throttles
    publishes per second to stay under IoT Core's per-connection limit.
    """

    def __init__(self, site_id: str):
        client_id = os.getenv("MQTT_CLIENT_ID", f"simulator-{site_id}")
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if os.getenv("MQTT_TLS_CERT"):
            self.client.tls_set(certfile=os.environ["MQTT_TLS_CERT"], keyfile=os.environ["MQTT_TLS_KEY"])
        self.client.connect(os.getenv("MQTT_HOST", "localhost"), int(os.getenv("MQTT_PORT", "1883")))
        self.client.loop_start()
        max_rate = float(os.getenv("MQTT_MAX_RATE") or 0)
        self.min_interval = 1.0 / max_rate if max_rate > 0 else 0.0
        self.last = None

    def publish(self, batch: list[tuple[str, bytes]]) -> None:
        for mqtt_topic, payload in batch:
            self.last = self.client.publish(mqtt_topic, payload, qos=1)
            if self.min_interval:
                time.sleep(self.min_interval)

    def close(self) -> None:
        if self.last is not None:
            self.last.wait_for_publish(timeout=30)
        self.client.loop_stop()
        self.client.disconnect()


class HttpGatewayPublisher:
    """One HTTPS POST per simulated hour carrying every reading, like a BMS gateway.

    Malformed payloads are forwarded as {"_unparseable": ...} so the API still dead-letters them.
    Retries with backoff on 5xx / network errors (a scaled-to-zero API takes a few seconds to wake).
    """

    def __init__(self, endpoint: str, api_key: str, max_attempts: int = 6):
        self.endpoint, self.api_key, self.max_attempts = endpoint, api_key, max_attempts

    def publish(self, batch: list[tuple[str, bytes]]) -> dict:
        body = json.dumps({"records": [decode(payload) for _, payload in batch]}).encode()
        request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json", "x-api-key": self.api_key},
        )
        delay = 2.0
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code < 500 or attempt == self.max_attempts:
                    raise
            except urllib.error.URLError:
                if attempt == self.max_attempts:
                    raise
            log.warning("ingest API unavailable (attempt %d), retrying in %.0fs", attempt, delay)
            time.sleep(delay)
            delay *= 2
        raise RuntimeError("unreachable")

    def close(self) -> None:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
