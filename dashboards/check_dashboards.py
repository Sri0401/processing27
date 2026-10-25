"""Smoke-test every dashboard query against the warehouse as the read-only BI role.

Catches broken SQL, missing columns and missing grants before a stakeholder does.
Grafana macros/variables are substituted with representative values.

    python dashboards/check_dashboards.py         # needs DB_* env and BI_DB_PASSWORD
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import psycopg

DASHBOARDS = Path(__file__).parent / "grafana" / "dashboards"

SUBSTITUTIONS = [
    (re.compile(r"\$__timeFilter\(([^)]+)\)"), r"\1 between '2016-01-01' and '2018-01-01'"),
    (re.compile(r"\$\{building:sqlstring\}"), "select building_id from marts.dim_building"),
]


def render(sql: str) -> str:
    for pattern, repl in SUBSTITUTIONS:
        sql = pattern.sub(repl, sql)
    return sql


def main() -> int:
    conninfo = (
        f"host={os.getenv('DB_HOST', 'localhost')} port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'smart_campus')} user=bi_reader password={os.environ['BI_DB_PASSWORD']}"
    )
    failures = 0
    with psycopg.connect(conninfo, autocommit=True) as conn:
        for path in sorted(DASHBOARDS.glob("*.json")):
            dashboard = json.loads(path.read_text(encoding="utf-8"))
            queries = [(p["title"], t["rawSql"]) for p in dashboard["panels"] for t in p.get("targets", [])]
            queries += [(f"variable {v['name']}", v["query"]) for v in dashboard["templating"]["list"]]
            for title, sql in queries:
                try:
                    rows = conn.execute(render(sql)).fetchall()
                    status = "ok   " if rows else "EMPTY"
                    print(f"{status} {dashboard['uid']:16} {title} ({len(rows)} rows)")
                except psycopg.Error as exc:
                    failures += 1
                    print(f"FAIL  {dashboard['uid']:16} {title}: {str(exc).splitlines()[0]}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
