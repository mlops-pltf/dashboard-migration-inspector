"""Markdown rendering of a report: blockers first, then unverified, warnings, info, and what was proven."""

from __future__ import annotations

from .report import Finding, ObjectRef, Report

_ICON = {"FAIL": "✗", "UNVERIFIED": "?", "PASS": "✓"}


def _ref(r: ObjectRef | None) -> str:
    if r is None:
        return "—"
    name = f"{r.model}::{r.name}" if r.model and r.name else (r.name or r.model or "—")
    loc = f" ({r.location})" if r.location else ""
    return f"`{name}`{loc}"


def _finding(f: Finding) -> str:
    lines = [f"### {_ICON[f.status]} {f.id} · {f.check} · {f.title}",
             f"- **Status:** {f.status} · **Severity:** {f.severity} · **Decided by:** {f.decided_by} · **Origin:** {f.origin}"
             + (" · **latent**" if f.latent else "")]
    lines.append(f"- **ThoughtSpot:** {_ref(f.thoughtspot)}")
    if f.thoughtspot and f.thoughtspot.definition:
        lines.append(f"  - `{f.thoughtspot.definition}`")
    lines.append(f"- **Strategy:** {_ref(f.strategy)}")
    if f.strategy and f.strategy.definition:
        lines.append(f"  - `{f.strategy.definition}`")
    if f.symptoms:
        lines.append(f"- **Also affects:** {', '.join(f.symptoms)}")
    if f.evidence_refs:
        lines.append(f"- **Evidence:** {', '.join(f.evidence_refs)}" + (f" · resolves {', '.join(f.resolves)}" if f.resolves else ""))
    for k, v in f.evidence.items():
        lines.append(f"- **{k}:** {v}")
    if f.needs:
        lines.append(f"- **To settle:** {', '.join(f.needs)}")
    if f.suggested_fix:
        lines.append(f"- **Suggested fix:** {f.suggested_fix}")
    return "\n".join(lines)


def markdown(report: Report) -> str:
    c = report.counts
    out = [f"# Inspector report · {report.package_id}",
           f"**Verdict: {report.verdict}** · stage: {report.stage} · run `{report.run_id}` · {report.created_at}",
           "",
           f"| Blockers failing | Blockers unverified | Warnings | Low | Info | Proven |",
           f"|---|---|---|---|---|---|",
           f"| {c.get('blocker_fail', 0)} | {c.get('blocker_unverified', 0)} | {c.get('warning', 0)} | "
           f"{c.get('low', 0)} | {c.get('info', 0)} | {c.get('pass', 0)} |", ""]
    if report.summary:
        out.extend(["## Summary", report.summary, ""])
    if report.coverage:
        out.append("**Coverage:** " + " · ".join(f"{k}: {v}" for k, v in report.coverage.items()))
        out.append("")
    groups = [
        ("Blockers", lambda f: f.severity == "BLOCKER" and f.status == "FAIL"),
        ("Unverified", lambda f: f.status == "UNVERIFIED"),
        ("Warnings", lambda f: f.severity == "WARNING" and f.status == "FAIL"),
        ("Low / info", lambda f: f.severity in ("LOW", "INFO") and f.status == "FAIL"),
    ]
    for title, pred in groups:
        items = [f for f in report.findings if pred(f)]
        if items:
            out.append(f"## {title} ({len(items)})")
            out.extend(_finding(f) + "\n" for f in items)
    proven = [f for f in report.findings if f.status == "PASS"]
    if proven:
        out.append(f"<details><summary>Proven ({len(proven)})</summary>\n")
        out.extend(f"- {f.check} {_ref(f.thoughtspot or f.strategy)}: {f.title}" for f in proven)
        out.append("\n</details>")
    if report.intentional_differences:
        out.append("\n## Intentional differences")
        out.extend(f"- `{d['thoughtspot']}`: {d.get('reason', '')}" for d in report.intentional_differences)
    if report.parse_issues:
        out.append("\n## Parse issues")
        out.extend(f"- {i['severity']}: {i['message']} ({i.get('source') or ''})" for i in report.parse_issues)
    return "\n".join(out) + "\n"
