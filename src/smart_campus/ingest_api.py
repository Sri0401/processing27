"""HTTPS ingest endpoint - the Azure stand-in for "IoT Hub -> Function".

Runs on Azure Container Apps (TLS terminated by the platform). Building gateways POST
batches of telemetry - one request per gateway per hour - which keeps request counts low
and matches how BMS gateways typically forward buffered data:

    POST /telemetry
    x-api-key: <key from Key Vault>
    {"records": [ {telemetry message}, ... ]}        (a single message object also works)

Each request is passed unchanged to the shared ingest handler (lambdas/ingest/handler.py).

    GET /healthz  -> 200 when the process is up (liveness)
    GET /readyz   -> 200 when the database is reachable (readiness)

Usage:  python -m smart_campus.ingest_api      (PORT, INGEST_API_KEY, DB_*, RAW_CONTAINER ...)
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import handler as ingest_handler

log = logging.getLogger("ingest_api")

MAX_BODY_BYTES = 1_000_000  # ~5,000 readings; larger gateway buffers must be split
API_KEY = os.getenv("INGEST_API_KEY", "")


class IngestRequestHandler(BaseHTTPRequestHandler):
    server_version = "smart-campus-ingest/1"

    def _reply(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path == "/healthz":
            self._reply(200, {"status": "ok"})
        elif self.path == "/readyz":
            try:
                db, _ = ingest_handler._sinks()
                db._connection().execute("select 1")
                self._reply(200, {"status": "ready"})
            except Exception as exc:  # report, don't crash the probe
                ingest_handler._db = None
                self._reply(503, {"status": "unavailable", "error": type(exc).__name__})
        else:
            self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/telemetry":
            return self._reply(404, {"error": "not_found"})
        if not API_KEY or not hmac.compare_digest(self.headers.get("x-api-key", ""), API_KEY):
            return self._reply(401, {"error": "unauthorized"})

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self.close_connection = True  # the unread body must not be parsed as a next request
            return self._reply(413 if length else 400, {"error": "invalid_body_size", "max_bytes": MAX_BODY_BYTES})
        try:
            event = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return self._reply(400, {"error": "invalid_json"})

        try:
            result = ingest_handler.handler(event)
        except Exception:
            log.exception("ingest failed")
            ingest_handler._db = None  # reconnect on the next request
            return self._reply(503, {"error": "ingest_unavailable"})  # gateway retries later
        self._reply(200, result)

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)


def main() -> None:
    if not API_KEY:
        raise SystemExit("INGEST_API_KEY must be set")
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), IngestRequestHandler)
    log.info("listening on :%d", port)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
