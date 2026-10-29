"""Deterministic checks (pipeline stage 5). Each module: ``run(ctx) -> list[Finding]``."""

from ...findings_ledger.report import Finding

from ..context import Context
from . import content, coverage, filters, joins, lineage, measures

CHECKS = (coverage, lineage, measures, joins, filters, content)


def run_all(ctx: Context) -> list[Finding]:
    out: list[Finding] = []
    for module in CHECKS:
        out.extend(module.run(ctx))
    return out
