"""Data probes: questions about the migration answered by the warehouse, through the data agent.

Every probe returns ``match``: True (the warehouse shows no difference), False (it shows one), or None (it
could not decide: something did not compile, the data agent is unreachable, or the answer depends on an
assumption). A probe never passes by default.

* kpi_equivalence: both sides' definitions compiled from their IR (query_compiler) and reconciled by the data
  agent, at the grand total and at every grain the measure is used at in ThoughtSpot content.
* key_profile: is a join key unique (does the join multiply rows)?
* orphan_profile: how many rows does a difference in join type actually affect today?
"""

from __future__ import annotations

from inspector_contract import FilterSpec
from ..package_inventory.loader import load_cached
from ..query_compiler.compile import compile_query
from ..rule_sweep.context import build
from .client import call

MAX_GRAINS = 6
ABS_TOL, REL_TOL = 0.01, 1e-6
_KEEPS = {"INNER": (False, False), "LEFT": (True, False), "RIGHT": (False, True), "FULL": (True, True)}


def _grains(pkg, ts_name: str) -> list[tuple[tuple[str, ...], tuple[FilterSpec, ...], str]]:
    """Grand total plus every (dimensions, filters) the measure is shown at in ThoughtSpot content."""
    out = [((), (), "grand total")]
    for d in pkg.thoughtspot.dashboards:
        for v in d.visualizations:
            if ts_name not in {m.field for m in v.query.measures}:
                continue
            filters = list(v.query.filters) + [FilterSpec(field=f.field, op=f.op, values=f.values)
                                               for f in d.filters if not f.scope or v.id in f.scope]
            dims = tuple(u.field for u in v.query.dimensions)
            key = (dims, tuple(filters))
            if all((g[0], g[1]) != key for g in out):
                out.append((dims, tuple(filters), f"{d.name} / {v.name}"))
    return out[:MAX_GRAINS]


def kpi_equivalence(package: str, thoughtspot: str, strategy: str, dimensions: list[str] | None = None) -> dict:
    pkg = load_cached(package)
    ctx = build(pkg)
    to_st = {m.ts: m.st for m in ctx.pairs}
    grains = [(tuple(dimensions), (), "requested")] if dimensions else _grains(pkg, thoughtspot)
    runs = []
    for dims, filters, label in grains:
        run = {"grain": label, "dimensions": list(dims),
               "filters": [f"{f.field} {f.op} {list(f.values)}" for f in filters]}
        missing = [n for n in list(dims) + [f.field for f in filters] if n not in to_st]
        if missing:
            run.update(outcome="skipped", reason=f"no Strategy counterpart for {missing}")
            runs.append(run)
            continue
        ts_q = compile_query(ctx.ts, [thoughtspot], list(dims), list(filters))
        st_filters = [f.model_copy(update={"field": to_st[f.field]}) for f in filters]
        st_dims = [to_st[d] for d in dims]
        variants = [("as exported", compile_query(ctx.st, [strategy], st_dims, st_filters))]
        if any("type unknown" in a for a in variants[0][1].assumptions):
            variants = [("unknown joins as LEFT", variants[0][1]),
                        ("unknown joins as INNER", compile_query(ctx.st, [strategy], st_dims, st_filters,
                                                                 unknown_join_as="INNER"))]
        unsupported = ts_q.unsupported + [u for _, q in variants for u in q.unsupported]
        if unsupported:
            run.update(outcome="not_compiled", reason="; ".join(dict.fromkeys(unsupported)))
            runs.append(run)
            continue
        results = []
        for label_v, q in variants:
            r = call("compare_queries", {"left_sql": ts_q.sql, "right_sql": q.sql,
                                         "keys": [f"d{i}" for i in range(len(dims))], "measures": ["m0"],
                                         "abs_tolerance": ABS_TOL, "rel_tolerance": REL_TOL})
            results.append((label_v, q, r))
        errors = [r["error"] for _, _, r in results if "error" in r]
        if errors:
            run.update(outcome="error", reason="; ".join(errors))
        else:
            statuses = {r["status"] for _, _, r in results}
            run["outcome"] = statuses.pop() if len(statuses) == 1 else "depends_on_join_type"
            run["comparison"] = {lv: _brief(r) for lv, _, r in results} if len(results) > 1 else _brief(results[0][2])
            run["assumptions"] = list(dict.fromkeys(a for _, q, _ in results for a in q.assumptions))
        run["sql"] = {"thoughtspot": ts_q.sql, "strategy": variants[0][1].sql}
        runs.append(run)

    outcomes = [r["outcome"] for r in runs]
    if "MISMATCH" in outcomes:
        match = False
    elif outcomes and all(o in ("MATCH", "EMPTY") for o in outcomes) and "MATCH" in outcomes:
        match = True
    else:
        match = None
    differing = [r["grain"] for r in runs if r["outcome"] == "MISMATCH"]
    summary = (f"{thoughtspot} differs at {', '.join(differing)}" if match is False else
               f"{thoughtspot} matches at every grain checked ({len(runs)})" if match is True else
               f"{thoughtspot}: not decided by data ({', '.join(sorted(set(outcomes)))})")
    return {"type": "kpi_equivalence", "match": match, "summary": summary,
            "subject": {"thoughtspot": thoughtspot, "strategy": strategy}, "runs": runs}


def _brief(r: dict) -> dict:
    m = r["measures"][0]
    return {"status": r["status"], "rows": r["rows"], "matching_rows": r["matching_rows"],
            "thoughtspot_only_rows": r["left_only_rows"], "strategy_only_rows": r["right_only_rows"],
            "thoughtspot_total": m["left_total"], "strategy_total": m["right_total"],
            "difference": m["total_difference"], "samples": r["mismatch_samples"][:5]}


def key_profile(source: str, keys: list[str], join: str | None = None) -> dict:
    r = call("key_profile", {"source": source, "keys": keys})
    if "error" in r:
        return {"type": "key_profile", "match": None, "summary": r["error"], "subject": {"join": join}}
    unique = bool(r["unique"])
    summary = (f"{source}({', '.join(keys)}) is unique: the join does not multiply rows" if unique else
               f"{source}({', '.join(keys)}) is not unique: {r['duplicate_keys']} keys repeat, up to "
               f"{r['max_rows_per_key']} rows each, so the join multiplies rows")
    return {"type": "key_profile", "match": unique, "summary": summary, "subject": {"join": join},
            "profile": r}


def orphan_profile(spec: dict) -> dict:
    """``spec``: join, left, right, left_keys, right_keys, thoughtspot_type, strategy_type, cardinality."""
    r = call("orphan_profile", {"left": spec["left"], "right": spec["right"], "left_keys": spec["left_keys"],
                                "right_keys": spec["right_keys"]})
    subject = {"join": spec.get("join")}
    if "error" in r:
        return {"type": "orphan_profile", "match": None, "summary": r["error"], "subject": subject}
    ts_t, st_t = spec.get("thoughtspot_type") or "LEFT", spec.get("strategy_type") or "UNKNOWN"
    left_o, right_o = r["left_side"]["orphan_rows"], r["right_side"]["orphan_rows"]
    assumptions = []
    if st_t == "UNKNOWN" or ts_t not in _KEEPS or st_t not in _KEEPS:
        if spec.get("cardinality") == "N:1":
            relevant = {"left": left_o}
            assumptions.append(f"join type unknown: only {spec['left']} rows without a match are counted "
                               f"({spec['right']} members without facts are not shown by either tool)")
        else:
            relevant = {"left": left_o, "right": right_o}
    elif ts_t == st_t:  # same type, different applicability (applied to every query vs only when needed)
        relevant = {"left": left_o}
    else:
        relevant = {}
        if _KEEPS[ts_t][0] != _KEEPS[st_t][0]:
            relevant["left"] = left_o
        if _KEEPS[ts_t][1] != _KEEPS[st_t][1]:
            relevant["right"] = right_o
    affected = sum(relevant.values())
    parts = [f"{n} {spec[side]} rows without a match in {spec['right' if side == 'left' else 'left']}"
             for side, n in relevant.items()]
    summary = ("no rows are affected today: " if affected == 0 else f"{affected} rows are affected: ") + \
        ("; ".join(parts) or "nothing to compare")
    unknown = st_t == "UNKNOWN" or ts_t not in _KEEPS or st_t not in _KEEPS
    if unknown and affected:
        summary += " (whether this changes a number depends on the join type, which the export does not carry)"
    match = True if affected == 0 else None if unknown else False
    return {"type": "orphan_profile", "match": match, "affected_rows": affected, "summary": summary,
            "subject": subject, "assumptions": assumptions, "profile": r}


def run_spec(package: str, spec: dict) -> dict:
    kind = spec.get("type")
    if kind == "kpi_equivalence":
        return kpi_equivalence(package, spec["thoughtspot"], spec["strategy"], spec.get("dimensions"))
    if kind == "key_profile":
        return key_profile(spec["source"], spec["keys"], spec.get("join"))
    if kind == "orphan_profile":
        return orphan_profile(spec)
    return {"type": kind, "match": None, "summary": f"unknown probe type {kind!r}", "subject": {}}
