"""Telemetry message contract (schema_version 1).

Topic:   campus/{site_id}/{building_id}/{meter}/telemetry
Payload: {"message_id": "<uuid>", "site_id": "Lamb", "building_id": "Lamb_education_Ora",
          "meter": "electricity", "ts": "2017-07-01T00:00:00Z", "value": 12.3,
          "unit": "kWh", "schema_version": 1}

Structural problems (missing fields, bad types, impossible values) are rejected here
and dead-lettered. Plausible-but-suspicious values (spikes, flat-lining) are accepted
and flagged downstream by dbt, because only the history can tell them apart.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime

SCHEMA_VERSION = 1
ALLOWED_METERS = frozenset({"electricity", "gas", "chilledwater", "hotwater", "steam", "water"})
ALLOWED_UNITS = frozenset({"kWh"})
REQUIRED_FIELDS = ("message_id", "site_id", "building_id", "meter", "ts", "value")
# Largest plausible hourly reading for a single campus building meter.
MAX_HOURLY_KWH = 50_000


class ValidationError(ValueError):
    """Raised when a message breaks the telemetry contract."""


@dataclass(frozen=True)
class Reading:
    message_id: str
    site_id: str
    building_id: str
    meter: str
    ts: datetime
    value: float
    unit: str

    @property
    def key(self) -> tuple[str, str, datetime]:
        return (self.building_id, self.meter, self.ts)


def validate(message: object) -> Reading:
    if not isinstance(message, dict):
        raise ValidationError("payload_not_object")
    if "_unparseable" in message:  # set by the bridge when the payload is not valid JSON
        raise ValidationError("unparseable_json")

    missing = [f for f in REQUIRED_FIELDS if message.get(f) in (None, "")]
    if missing:
        raise ValidationError(f"missing_field:{','.join(missing)}")

    if message.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValidationError("unsupported_schema_version")

    try:
        message_id = str(uuid.UUID(str(message["message_id"])))
    except ValueError as exc:
        raise ValidationError("invalid_message_id") from exc

    site_id, building_id = str(message["site_id"]), str(message["building_id"])
    if not building_id.startswith(f"{site_id}_"):
        raise ValidationError("building_not_in_site")

    meter = str(message["meter"])
    if meter not in ALLOWED_METERS:
        raise ValidationError("unknown_meter")

    unit = str(message.get("unit", "kWh"))
    if unit not in ALLOWED_UNITS:
        raise ValidationError("unsupported_unit")

    try:
        ts = datetime.fromisoformat(str(message["ts"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("invalid_timestamp") from exc
    if ts.tzinfo is None:
        raise ValidationError("timestamp_without_timezone")

    value = message["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("value_not_numeric")
    value = float(value)
    if not math.isfinite(value):
        raise ValidationError("value_not_finite")
    if value < 0:
        raise ValidationError("negative_consumption")
    if value > MAX_HOURLY_KWH:
        raise ValidationError("value_out_of_range")

    return Reading(message_id, site_id, building_id, meter, ts, value, unit)
