"""CV-01 / CV-02: every ThoughtSpot field has a counterpart; extra Strategy fields are listed."""

from __future__ import annotations

from ...findings_ledger.report import Finding

from ..context import Context
from .common import st_ref, ts_ref


def run(ctx: Context) -> list[Finding]:
    out = []
    for m in ctx.matches:
        if m.ts and not m.st:
            used = m.ts in ctx.used_in_content
            ambiguous = m.basis == "ambiguous"
            out.append(Finding(
                check="CV-01", severity="BLOCKER" if used or ambiguous else "WARNING",
                status="UNVERIFIED" if ambiguous else "FAIL",
                title=(f"{m.ts} has several possible counterparts" if ambiguous
                       else f"{m.ts} has no counterpart in Strategy"),
                thoughtspot=ts_ref(ctx, m.ts),
                evidence={"used in ThoughtSpot content": "yes" if used else "no",
                          **({"candidates": ", ".join(m.candidates)} if ambiguous else {})},
                needs=["llm:mapping"] if ambiguous else [],
                suggested_fix=None if ambiguous else
                f"Add {m.ts} to the Strategy model, or list it under `ignore` in mapping.yaml with a reason."))
        elif m.st and not m.ts:
            out.append(Finding(check="CV-02", severity="INFO", status="FAIL",
                               title=f"{m.st} exists only in Strategy", strategy=st_ref(ctx, m.st)))
    return out
