"""Runtime configuration, read from environment variables (12-factor style)."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw" / "bdg2"
STAGED_DIR = DATA_DIR / "staged"
SYNTHETIC_DIR = DATA_DIR / "synthetic"

SITE_ID = os.getenv("SITE_ID", "Lamb")
METERS = ("electricity", "gas")


def site_dir(site_id: str = SITE_ID) -> Path:
    return STAGED_DIR / site_id


def db_conninfo() -> str:
    """libpq connection string. Pipelines run as the least-privilege `ingest_writer`."""
    return (
        f"host={os.getenv('DB_HOST', 'localhost')} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'smart_campus')} "
        f"user={os.getenv('DB_USER', 'ingest_writer')} "
        f"password={os.environ['DB_PASSWORD']}"
    )
