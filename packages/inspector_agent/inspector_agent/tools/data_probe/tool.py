"""Tool: request_data_probe. Runs data probes through the data agent (see probes.py)."""

from __future__ import annotations

from strands import tool

from ..rule_sweep.tool import sweep
from .probes import key_profile, kpi_equivalence, orphan_profile, run_spec
from .settle import _decide

PROBE_TYPES = ("lead", "kpi_equivalence", "key_profile", "orphan_profile")


def request_data_probe_impl(package: str, probe_type: str, lead_id: str | None = None,
                            thoughtspot_field: str | None = None, strategy_field: str | None = None,
                            dimensions: list[str] | None = None, source: str | None = None,
                            keys: list[str] | None = None, join: str | None = None) -> dict:
    if probe_type not in PROBE_TYPES:
        return {"error": f"probe_type must be one of {PROBE_TYPES}"}
    if probe_type == "lead":
        lead = next((f for f in sweep(package) if f.id == lead_id), None)
        if lead is None:
            return {"error": f"unknown lead {lead_id!r}"}
        if not lead.probes:
            return {"match": None, "summary": f"{lead_id} has no data probe; data cannot settle it",
                    "subject": {"lead": lead_id}}
        results = [run_spec(package, spec) for spec in lead.probes]
        return {"type": "lead", "match": _decide(results), "summary": "; ".join(r["summary"] for r in results),
                "subject": {"lead": lead_id, "thoughtspot": lead.thoughtspot.name if lead.thoughtspot else None,
                            "strategy": lead.strategy.name if lead.strategy else None},
                "results": results}
    if probe_type == "kpi_equivalence":
        if not (thoughtspot_field and strategy_field):
            return {"error": "kpi_equivalence needs thoughtspot_field and strategy_field"}
        return kpi_equivalence(package, thoughtspot_field, strategy_field, dimensions)
    if probe_type == "key_profile":
        if not (source and keys):
            return {"error": "key_profile needs source (schema.table or SELECT) and keys"}
        return key_profile(source, keys, join)
    spec = next((p for f in sweep(package) for p in f.probes
                 if p.get("type") == "orphan_profile" and p.get("join") == join), None)
    if spec is None:
        return {"error": f"no join {join!r} with a different type or applicability among the leads; use "
                         "key_profile or ask the data agent directly"}
    return orphan_profile(spec)


@tool
def request_data_probe(package: str, probe_type: str, lead_id: str | None = None,
                       thoughtspot_field: str | None = None, strategy_field: str | None = None,
                       dimensions: list[str] | None = None, source: str | None = None,
                       keys: list[str] | None = None, join: str | None = None) -> dict:
    """Settle a question with the warehouse, through the data agent. The result's `match` is true (no difference
    in the data), false (a difference, with its size) or null (data could not decide, and why).

    Probe types:
    - lead: run the probes a rule-sweep lead carries (lead_id). Simplest way to settle an open lead.
    - kpi_equivalence: compile both sides' definitions of a measure to SQL and compare the numbers at the grand
      total and at every grain the measure is shown at in ThoughtSpot content (or at `dimensions`, ThoughtSpot
      names). Needs thoughtspot_field and strategy_field.
    - key_profile: is `keys` unique in `source` (schema.table or SELECT)? A non-unique key multiplies rows.
    - orphan_profile: how many rows does a join-type difference affect today (`join`: ThoughtSpot join name).

    Args:
        package: Path of the package folder or zip.
        probe_type: lead | kpi_equivalence | key_profile | orphan_profile.
        lead_id: Rule-sweep lead id, for probe_type lead.
        thoughtspot_field: ThoughtSpot measure, for kpi_equivalence.
        strategy_field: Strategy counterpart, for kpi_equivalence.
        dimensions: ThoughtSpot attributes to compare by, for kpi_equivalence (default: content grains).
        source: schema.table or SELECT, for key_profile.
        keys: Key columns, for key_profile.
        join: ThoughtSpot join name, for orphan_profile.
    """
    return request_data_probe_impl(package, probe_type, lead_id, thoughtspot_field, strategy_field, dimensions,
                                   source, keys, join)
