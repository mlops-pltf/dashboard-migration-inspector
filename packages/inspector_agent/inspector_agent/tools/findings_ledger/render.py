"""Markdown rendering of a report. The words people read are the agent's (brief, titles, explanations, fixes),
shown as written; code adds only facts: the verdict, counts, a neutral label per item, and a folded "Details for
developers" with check, finding, evidence and lead ids, file locations and definitions."""

from __future__ import annotations

from .plain import VERDICT, label
from .report import Finding, ObjectRef, Report


def _name(r: ObjectRef | None) -> str | None:
    return r.name if r and r.name else None


def _ref(r: ObjectRef | None) -> str:
    if r is None:
        return "—"
    name = f"{r.model}::{r.name}" if r.model and r.name else (r.name or r.model or "—")
    return f"`{name}`" + (f" ({r.location})" if r.location else "")


def _affects(f: Finding) -> list[str]:
    ts, st = _name(f.thoughtspot), _name(f.strategy)
    first = ts or st
    if ts and st and ts != st:
        first = f"{ts} (Strategy: {st})"
    return [x for x in [first, *f.symptoms] if x]


def _developer_details(f: Finding) -> str:
    lines = [f"- Check: {f.check} · finding {f.id} · {f.status} {f.severity}"
             f" · decided by {f.decided_by}",
             f"- ThoughtSpot: {_ref(f.thoughtspot)}"]
    if f.thoughtspot and f.thoughtspot.definition:
        lines.append(f"  - `{f.thoughtspot.definition}`")
    lines.append(f"- Strategy: {_ref(f.strategy)}")
    if f.strategy and f.strategy.definition:
        lines.append(f"  - `{f.strategy.definition}`")
    if f.evidence_refs or f.resolves:
        lines.append(f"- Evidence: {', '.join(f.evidence_refs) or '—'}" + (f" · resolves {', '.join(f.resolves)}" if f.resolves else ""))
    for k, v in f.evidence.items():
        if k not in ("explanation", "data_probes"):
            lines.append(f"- {k}: {v}")
    if f.needs:
        lines.append(f"- To settle: {', '.join(f.needs)}")
    return "<details><summary>Details for developers</summary>\n\n" + "\n".join(lines) + "\n\n</details>"


def _finding(n: int, f: Finding) -> str:
    lines = [f"### {n}. {f.title}",
             f"**{label(f.severity, f.status)}**" + (" · no impact on today's data" if f.latent else ""), ""]
    if affects := _affects(f):
        lines.append(f"**Affects:** {', '.join(affects)}  ")
    if why := f.evidence.get("explanation"):
        lines.append(f"**Why it matters:** {why}  ")
    if f.suggested_fix:
        lines.append(f"**What to do:** {f.suggested_fix}  ")
    lines.extend(["", _developer_details(f)])
    return "\n".join(lines)


SECTIONS = [
    ("Must fix before go-live", lambda f: f.severity == "BLOCKER" and f.status == "FAIL"),
    ("Could not be confirmed", lambda f: f.status == "UNVERIFIED"),
    ("Worth checking", lambda f: f.severity == "WARNING" and f.status == "FAIL"),
    ("Cosmetic and for information", lambda f: f.severity in ("LOW", "INFO") and f.status == "FAIL"),
]


def markdown(report: Report) -> str:
    c = report.counts
    out = [f"# Migration check: {report.package_id}", "",
           f"## {VERDICT.get(report.verdict, report.verdict)}", "",
           f"- Must fix: **{c.get('blocker_fail', 0)}** · Could not confirm: **{c.get('blocker_unverified', 0)}**"
           f" · Worth checking: **{c.get('warning', 0)}** · Cosmetic / information: **{c.get('low', 0) + c.get('info', 0)}**"
           f" · Confirmed: **{c.get('pass', 0)}**",
           f"- Run {report.run_id} · {report.created_at} · {report.stage}", ""]
    if report.summary:
        out.extend(["## In short", report.summary, ""])
    if report.developer_notes:
        out.extend(["## Notes given with the run", "*Context for the review, not proof: nothing below passes "
                    "because of them.*", "", report.developer_notes, ""])
    n = 0
    for title, pred in SECTIONS:
        items = [f for f in report.findings if pred(f)]
        if items:
            out.append(f"## {title} ({len(items)})\n")
            for f in items:
                n += 1
                out.append(_finding(n, f) + "\n")
    proven = [f for f in report.findings if f.status == "PASS"]
    if proven:
        out.append(f"<details><summary>What was confirmed ({len(proven)})</summary>\n")
        out.extend(f"- {f.title}" for f in proven)
        out.append("\n</details>\n")
    if report.intentional_differences:
        out.append("## Differences declared intentional")
        out.extend(f"- {d['thoughtspot']}: {d.get('reason', '')}" for d in report.intentional_differences)
        out.append("")
    if report.parse_issues:
        out.append("<details><summary>Notes from reading the files (for developers)</summary>\n")
        out.extend(f"- {i['severity']}: {i['message']} ({i.get('source') or ''})" for i in report.parse_issues)
        out.append("\n</details>")
    return "\n".join(out).rstrip() + "\n"
