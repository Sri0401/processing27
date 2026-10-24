"""Output sinks for the ingest handler: S3 raw archive and PostgreSQL (Aurora)."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

from validation import Reading

UPSERT_SQL = """
INSERT INTO raw.meter_reading (site_id, building_id, meter, ts, value, unit, source, message_id)
VALUES (%s, %s, %s, %s, %s, %s, 'stream', %s)
ON CONFLICT (building_id, meter, ts) DO UPDATE
SET value = EXCLUDED.value,
    message_id = EXCLUDED.message_id,
    source = EXCLUDED.source,
    ingested_at = now()
WHERE raw.meter_reading.value IS DISTINCT FROM EXCLUDED.value
"""

REJECT_SQL = "INSERT INTO raw.telemetry_reject (reason, payload) VALUES (%s, %s)"


class RawArchive:
    """Writes every received message (valid or not) to the S3 landing zone as JSON Lines.

    Partitioned by *receipt* time so the archive is append-only and replayable.
    """

    def __init__(self, bucket: str, client=None):
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL"))
        self.bucket = bucket
        self.client = client

    def write(self, records: list, received_at: datetime | None = None) -> str:
        received_at = received_at or datetime.now(UTC)
        key = (
            f"telemetry/dt={received_at:%Y-%m-%d}/hour={received_at:%H}/"
            f"{received_at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.jsonl"
        )
        body = "\n".join(json.dumps(r, default=str) for r in records).encode()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body)
        return key


def resolve_conninfo() -> str:
    """Local: DB_* env vars. AWS: an RDS-format secret in Secrets Manager (DB_SECRET_ARN)."""
    secret_arn = os.getenv("DB_SECRET_ARN")
    if secret_arn:
        import boto3

        secret = json.loads(
            boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
        )
        return (
            f"host={secret['host']} port={secret.get('port', 5432)} "
            f"dbname={secret.get('dbname', 'smart_campus')} "
            f"user={secret['username']} password={secret['password']} sslmode=require"
        )
    return (
        f"host={os.getenv('DB_HOST', 'localhost')} port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'smart_campus')} "
        f"user={os.getenv('DB_USER', 'ingest_writer')} password={os.environ['DB_PASSWORD']}"
    )


class PostgresSink:
    def __init__(self, conninfo: str | None = None):
        self.conninfo = conninfo or resolve_conninfo()
        self._conn = None

    def _connection(self):
        import psycopg

        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.conninfo, autocommit=False)
        return self._conn

    def write(self, readings: list[Reading], rejects: list[tuple[str, object]]) -> None:
        conn = self._connection()
        try:
            with conn.cursor() as cur:
                if readings:
                    cur.executemany(
                        UPSERT_SQL,
                        [
                            (r.site_id, r.building_id, r.meter, r.ts, r.value, r.unit, r.message_id)
                            for r in readings
                        ],
                    )
                if rejects:
                    cur.executemany(
                        REJECT_SQL,
                        [(reason, json.dumps(payload, default=str)) for reason, payload in rejects],
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
