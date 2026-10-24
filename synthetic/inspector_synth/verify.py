"""Run every witness pair against the warehouse and check that defects show and benign changes don't."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg
import yaml

DEFAULT_DSN = os.environ.get(
    "INSPECTOR_WAREHOUSE_DSN", "postgresql://inspector_ro:inspector_ro@localhost:5439/analytics")


@dataclass
class Outcome:
    scenario: str
    witness: str
    expect: str
    actual: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.expect == self.actual


def _norm(value):
    if isinstance(value, (Decimal, float)):
        return round(float(value), 6)
    return value


def _rows(cur, sql: str) -> list[tuple]:
    cur.execute(sql)
    return [tuple(_norm(v) for v in row) for row in cur.fetchall()]


def _key(row: tuple) -> tuple:
    return tuple((v is None, str(v)) for v in row)


def compare(a: list[tuple], b: list[tuple]) -> tuple[str, str]:
    if sorted(a, key=_key) == sorted(b, key=_key):
        return "equal", f"{len(a)} rows identical"
    only_a = len(set(a) - set(b))
    only_b = len(set(b) - set(a))
    return "differ", f"{len(a)} vs {len(b)} rows; {only_a} only in ThoughtSpot, {only_b} only in Strategy"


def run(ground_truth: Path, dsn: str = DEFAULT_DSN) -> list[Outcome]:
    outcomes = []
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for gt_file in sorted(ground_truth.glob("*/expected_findings.yaml")):
            doc = yaml.safe_load(gt_file.read_text())
            for w in doc.get("witnesses", []):
                ts = _rows(cur, (gt_file.parent / w["thoughtspot_sql"]).read_text())
                st = _rows(cur, (gt_file.parent / w["strategy_sql"]).read_text())
                actual, detail = compare(ts, st)
                outcomes.append(Outcome(doc["scenario"], w["id"], w["expect"], actual, detail))
    return outcomes
