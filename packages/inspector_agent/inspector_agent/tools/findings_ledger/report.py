"""Findings and the validation report (stable JSON schema)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field as PField

Severity = Literal["BLOCKER", "WARNING", "LOW", "INFO"]
Status = Literal["FAIL", "PASS", "UNVERIFIED"]
Verdict = Literal["READY", "NOT_READY", "NEEDS_REVIEW"]

SEVERITY_ORDER = {"BLOCKER": 0, "WARNING": 1, "LOW": 2, "INFO": 3}
STATUS_ORDER = {"FAIL": 0, "UNVERIFIED": 1, "PASS": 2}


class ObjectRef(BaseModel):
    model: str | None = None
    name: str | None = None
    kind: str = "field"  # field | join | filter | model | relation
    location: str | None = None
    definition: str | None = None


class Finding(BaseModel):
    id: str = ""
    check: str
    severity: Severity
    status: Status
    title: str
    thoughtspot: ObjectRef | None = None
    strategy: ObjectRef | None = None
    match_basis: str | None = None  # mapped | exact | inferred
    decided_by: str = "rule"  # rule | llm | data | rule+data | llm+data
    evidence: dict[str, Any] = {}
    symptoms: list[str] = []  # other objects affected by the same root cause
    latent: bool = False
    needs: list[str] = []  # what would settle an UNVERIFIED finding, e.g. data:key_profile
    probes: list[dict[str, Any]] = []  # data probes that settle or size it (run by data_probe)
    suggested_fix: str | None = None
    origin: str = "rule"  # rule (rule sweep) | agent (recorded by the agent)
    evidence_refs: list[str] = []  # evidence ids from the run's ledger
    resolves: list[str] = []  # rule-sweep lead ids this finding settles

    @property
    def blocking(self) -> bool:
        return self.severity == "BLOCKER" and self.status in ("FAIL", "UNVERIFIED")


class Report(BaseModel):
    run_id: str
    package_id: str
    created_at: str = PField(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    verdict: Verdict
    counts: dict[str, int] = {}
    coverage: dict[str, Any] = {}
    inputs: dict[str, Any] = {}
    findings: list[Finding] = []
    intentional_differences: list[dict[str, str]] = []
    parse_issues: list[dict[str, Any]] = []
    stage: str = "static"  # rules-only | agent | data-verified
    summary: str = ""  # the agent's executive summary
    developer_notes: str = ""  # instructions the developer gave the run: context, never proof
    agent: dict[str, Any] = {}  # model, tool calls, evidence count


def verdict_of(findings: list[Finding]) -> Verdict:
    if any(f.severity == "BLOCKER" and f.status == "FAIL" for f in findings):
        return "NOT_READY"
    if any(f.blocking or (f.severity == "WARNING" and f.status != "PASS") for f in findings):
        return "NEEDS_REVIEW"
    return "READY"


def counts_of(findings: list[Finding]) -> dict[str, int]:
    out = {"blocker_fail": 0, "blocker_unverified": 0, "warning": 0, "low": 0, "info": 0, "pass": 0}
    for f in findings:
        if f.status == "PASS":
            out["pass"] += 1
        elif f.severity == "BLOCKER":
            out["blocker_fail" if f.status == "FAIL" else "blocker_unverified"] += 1
        else:
            out[f.severity.lower()] += 1
    return out


def finalize(findings: list[Finding]) -> list[Finding]:
    """Stable order (worst first) and ids."""
    ordered = sorted(findings, key=lambda f: (STATUS_ORDER[f.status], SEVERITY_ORDER[f.severity], f.check,
                                              (f.thoughtspot or f.strategy or ObjectRef()).name or ""))
    for i, f in enumerate(ordered, 1):
        f.id = f"F-{i:03d}"
    return ordered
