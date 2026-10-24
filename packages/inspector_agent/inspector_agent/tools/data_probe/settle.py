"""Settle rule-sweep leads with data (the deterministic "rules + data" stage).

* UNVERIFIED -> PASS or FAIL when the lead's probes decide (a key is unique or not; both definitions return the
  same numbers or not; a join-type question affects no rows). Otherwise it stays UNVERIFIED, with what was seen.
* FAIL stays FAIL: a proven difference in the definitions is a defect even when today's data hides it. Data
  marks it ``latent`` (no impact today) or records its impact.
* PASS is never touched.
"""

from __future__ import annotations

from ..findings_ledger.report import Finding
from .probes import run_spec


def _decide(results: list[dict]) -> bool | None:
    matches = [r["match"] for r in results]
    if any(m is False for m in matches):
        return False
    if matches and all(m is True for m in matches):
        return True
    return None


def settle(package: str, leads: list[Finding]) -> list[Finding]:
    for f in leads:
        if f.status == "PASS" or not f.probes:
            continue
        results = [run_spec(package, spec) for spec in f.probes]
        f.evidence = {**f.evidence, "data": [r["summary"] for r in results]}
        f.evidence["data_probes"] = results
        decided = _decide(results)
        if decided is None:
            continue
        f.decided_by = "rule+data"
        if f.status == "UNVERIFIED":
            f.status = "PASS" if decided else "FAIL"
            f.title += " (data: no difference)" if decided else " (confirmed by data)"
            f.needs = []
        elif f.status == "FAIL" and decided:
            f.latent = True
            f.title += " (no impact on today's data)"
    return leads
