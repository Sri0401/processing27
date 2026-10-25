"""Payload helpers shared by the MQTT bridge and the HTTPS gateway publisher."""

from __future__ import annotations

import json


def decode(payload: bytes) -> object:
    """Parse a message; unparseable payloads are passed through so they get dead-lettered."""
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_unparseable": payload.decode("utf-8", errors="replace")}
