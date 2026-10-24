"""Telemetry ingest Lambda.

AWS:   IoT Core topic rule  SELECT * FROM 'campus/+/+/+/telemetry'  ->  this Lambda
       (one message per invocation).
Local: smart_campus.bridge subscribes to Mosquitto and invokes this handler with
       micro-batches: {"records": [msg, msg, ...]}.

Flow per invocation:
  1. archive every received message to the S3 raw zone (source of truth for replay)
  2. validate against the telemetry contract (validation.py)
  3. idempotently upsert valid readings; dead-letter invalid ones
"""

from __future__ import annotations

import json
import logging
import os

from sinks import PostgresSink, RawArchive
from validation import Reading, ValidationError, validate

log = logging.getLogger()
log.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Created once per execution environment and reused across warm invocations.
_db: PostgresSink | None = None
_archive: RawArchive | None = None


def _sinks() -> tuple[PostgresSink, RawArchive | None]:
    global _db, _archive
    if _db is None:
        _db = PostgresSink()
    bucket = os.getenv("RAW_BUCKET")
    if _archive is None and bucket:
        _archive = RawArchive(bucket)
    return _db, _archive


def split(records: list) -> tuple[list[Reading], list[tuple[str, object]]]:
    """Validate records; de-duplicate on (building, meter, ts), keeping the last."""
    valid: dict[tuple, Reading] = {}
    rejects: list[tuple[str, object]] = []
    for record in records:
        try:
            reading = validate(record)
        except ValidationError as exc:
            rejects.append((str(exc), record))
            continue
        valid[reading.key] = reading
    return list(valid.values()), rejects


def handler(event, context=None, db: PostgresSink | None = None, archive=None) -> dict:
    records = event["records"] if isinstance(event, dict) and "records" in event else [event]
    if db is None:
        db, archive = _sinks()

    if archive is not None:
        archive.write(records)

    readings, rejects = split(records)
    db.write(readings, rejects)

    result = {"received": len(records), "accepted": len(readings), "rejected": len(rejects)}
    if rejects:
        log.warning(json.dumps({**result, "reasons": sorted({r for r, _ in rejects})}))
    return result
