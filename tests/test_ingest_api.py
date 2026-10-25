import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import handler
import sinks
from smart_campus import ingest_api
from smart_campus.simulator import HttpGatewayPublisher, build_message
from test_handler import FakeArchive, FakeDb
from test_validation import message

API_KEY = "test-key"


@pytest.fixture()
def api(monkeypatch):
    """Run the real HTTP server on a free port with fake sinks behind the shared handler."""
    db, archive = FakeDb(), FakeArchive()
    monkeypatch.setattr(ingest_api, "API_KEY", API_KEY)
    monkeypatch.setattr(handler, "_sinks", lambda: (db, archive))
    server = ThreadingHTTPServer(("127.0.0.1", 0), ingest_api.IngestRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", db, archive
    server.shutdown()


def post(url: str, body: bytes, key: str = API_KEY) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "x-api-key": key})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def test_batch_is_ingested_through_shared_handler(api):
    url, db, archive = api
    status, body = post(f"{url}/telemetry", json.dumps({"records": [message(), message(value=-1)]}).encode())
    assert status == 200
    assert body == {"received": 2, "accepted": 1, "rejected": 1}
    assert len(archive.batches) == 1 and db.rejects[0][0] == "negative_consumption"


@pytest.mark.parametrize(("key", "body", "expected"), [
    ("wrong-key", b"{}", 401),
    (API_KEY, b"not json", 400),
], ids=["bad-key", "bad-json"])
def test_bad_requests_are_refused_before_the_handler(api, key, body, expected):
    url, db, archive = api
    status, _ = post(f"{url}/telemetry", body, key=key)
    assert status == expected
    assert archive.batches == []


def test_oversized_body_is_refused_without_reading_it(api):
    """Only the headers are sent: the API must answer 413 from Content-Length alone."""
    import http.client

    url, _, archive = api
    conn = http.client.HTTPConnection(url.removeprefix("http://"), timeout=5)
    conn.putrequest("POST", "/telemetry")
    conn.putheader("x-api-key", API_KEY)
    conn.putheader("Content-Length", str(ingest_api.MAX_BODY_BYTES + 1))
    conn.endheaders()
    response = conn.getresponse()
    assert response.status == 413
    assert response.getheader("Connection") == "close" or response.will_close
    assert archive.batches == []


def test_database_failure_returns_503_so_gateway_retries(api, monkeypatch):
    url, _, _ = api

    def boom(event):
        raise ConnectionError("db down")

    monkeypatch.setattr(handler, "handler", boom)
    status, body = post(f"{url}/telemetry", json.dumps(message()).encode())
    assert (status, body["error"]) == (503, "ingest_unavailable")


def test_healthz(api):
    url, _, _ = api
    with urllib.request.urlopen(f"{url}/healthz") as response:
        assert json.load(response) == {"status": "ok"}


def test_gateway_publisher_posts_one_batch_with_malformed_payloads_forwarded(api):
    url, db, archive = api
    import pandas as pd

    ts = pd.Timestamp("2017-07-01T05:00:00Z")
    good = json.dumps(build_message("Lamb", "Lamb_office_Joy", "gas", ts, 1.5)).encode()
    result = HttpGatewayPublisher(f"{url}/telemetry", API_KEY).publish([("t", good), ("t", b'{"value": ')])
    assert result == {"received": 2, "accepted": 1, "rejected": 1}
    assert db.rejects[0][0] == "unparseable_json"


def test_gateway_publisher_does_not_retry_client_errors(api):
    url, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as exc:
        HttpGatewayPublisher(f"{url}/telemetry", "wrong-key").publish([("t", b"{}")])
    assert exc.value.code == 401


class FakeContainer:
    def __init__(self):
        self.blobs = {}

    def upload_blob(self, name, data):
        self.blobs[name] = data


def test_blob_archive_uses_same_layout_as_s3():
    container = FakeContainer()
    archive = sinks.BlobArchive("https://example.blob.core.windows.net/", "raw", client=container)
    key = archive.write([{"a": 1}, {"b": 2}])
    assert key.startswith("telemetry/dt=") and key.endswith(".jsonl")
    assert container.blobs[key] == b'{"a": 1}\n{"b": 2}'


def test_raw_archive_is_selected_by_configuration(monkeypatch):
    for var in ("AZURE_STORAGE_ACCOUNT_URL", "RAW_CONTAINER", "RAW_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    assert sinks.raw_archive_from_env() is None

    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_URL", "https://example.blob.core.windows.net/")
    monkeypatch.setenv("RAW_CONTAINER", "raw")
    monkeypatch.setattr(sinks, "BlobArchive", lambda url, container: ("blob", url, container))
    assert sinks.raw_archive_from_env() == ("blob", "https://example.blob.core.windows.net/", "raw")
