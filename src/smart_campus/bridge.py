"""Local stand-in for "IoT Core topic rule -> Lambda".

Subscribes to Mosquitto, micro-batches messages and invokes the real ingest handler
(lambdas/ingest/handler.py). On failure the batch is retried with backoff, so a
database restart delays data rather than losing it.

Usage:  python -m smart_campus.bridge
"""

from __future__ import annotations

import json
import logging
import os
import queue
import time

import paho.mqtt.client as mqtt

import handler as ingest_handler

log = logging.getLogger("bridge")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "campus/+/+/+/telemetry")
BATCH_MAX = int(os.getenv("BATCH_MAX", "1000"))
BATCH_SECONDS = float(os.getenv("BATCH_SECONDS", "1.0"))
MAX_BACKOFF_SECONDS = 30


def decode(payload: bytes) -> object:
    """Parse a message; unparseable payloads are passed through so they get dead-lettered."""
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_unparseable": payload.decode("utf-8", errors="replace")}


def ensure_bucket() -> None:
    """Locally the bucket may not exist yet; in AWS it is created by CDK."""
    bucket = os.getenv("RAW_BUCKET")
    if not bucket or not os.getenv("S3_ENDPOINT_URL"):
        return
    import boto3

    s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT_URL"])
    if bucket not in {b["Name"] for b in s3.list_buckets().get("Buckets", [])}:
        s3.create_bucket(Bucket=bucket)
        log.info("created bucket %s", bucket)


def invoke_with_retry(batch: list) -> dict:
    delay = 1.0
    while True:
        try:
            return ingest_handler.handler({"records": batch})
        except Exception:
            log.exception("ingest failed for batch of %d, retrying in %.0fs", len(batch), delay)
            ingest_handler._db = None  # force a fresh connection
            time.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_SECONDS)


def main() -> None:
    inbox: queue.Queue = queue.Queue(maxsize=100_000)

    def on_connect(client, userdata, flags, reason_code, properties):
        log.info("connected to mqtt://%s:%d (%s), subscribing %s",
                 MQTT_HOST, MQTT_PORT, reason_code, MQTT_TOPIC)
        client.subscribe(MQTT_TOPIC, qos=1)

    def on_message(client, userdata, msg):
        inbox.put(decode(msg.payload))

    ensure_bucket()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ingest-bridge", clean_session=False)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()

    totals = {"received": 0, "accepted": 0, "rejected": 0}
    while True:
        batch, deadline = [], time.monotonic() + BATCH_SECONDS
        while len(batch) < BATCH_MAX and (remaining := deadline - time.monotonic()) > 0:
            try:
                batch.append(inbox.get(timeout=remaining))
            except queue.Empty:
                break
        if not batch:
            continue
        result = invoke_with_retry(batch)
        for k in totals:
            totals[k] += result[k]
        log.info("batch %s | totals %s", result, totals)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
