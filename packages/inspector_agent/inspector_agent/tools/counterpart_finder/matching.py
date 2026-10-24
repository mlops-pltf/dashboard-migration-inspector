"""Object matching: mapping file first, then exact names (incl. synonyms), then inference.

Inference scores candidates by shared physical columns (strongest signal), name similarity and kind.
Close calls are marked ``ambiguous`` and left for the MappingJudge (an LLM) instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from inspector_contract import MigrationPackage, SemanticModel
from ..lineage_tracer.resolve import Resolved

THRESHOLD = 0.6
AMBIGUITY_MARGIN = 0.05


@dataclass(frozen=True)
class FieldMatch:
    ts: str | None
    st: str | None
    basis: str | None  # mapped | exact | inferred | ambiguous | None (unmatched)
    score: float = 1.0
    candidates: tuple[str, ...] = ()


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", name.lower()).strip()


def pick_models(pkg: MigrationPackage) -> tuple[SemanticModel, SemanticModel]:
    ts_models, st_models = pkg.thoughtspot.models, pkg.strategy.models
    if not ts_models or not st_models:
        raise ValueError("package needs one ThoughtSpot and one Strategy model")
    if pkg.mapping and pkg.mapping.models:
        for e in pkg.mapping.models:
            ts = next((m for m in ts_models if m.name == e.thoughtspot), None)
            st = next((m for m in st_models if m.name == e.strategy), None)
            if ts and st:
                return ts, st
    for ts in ts_models:
        st = next((m for m in st_models if norm(m.name) == norm(ts.name)), None)
        if st:
            return ts, st
    return ts_models[0], st_models[0]


def _score(a: Resolved, b: Resolved, a_name: str, b_name: str) -> float:
    if a.kind != b.kind:
        return 0.0
    cols_a = set(a.columns)
    cols_b = set(b.equivalent_columns or b.columns)
    jaccard = len(cols_a & cols_b) / len(cols_a | cols_b) if cols_a | cols_b else 0.0
    name = SequenceMatcher(None, norm(a_name), norm(b_name)).ratio()
    return 0.6 * jaccard + 0.4 * name


def match_fields(pkg: MigrationPackage, ts: SemanticModel, st: SemanticModel, ts_res: dict[str, Resolved],
                 st_res: dict[str, Resolved], ignored: dict[str, str]) -> list[FieldMatch]:
    out: list[FieldMatch] = []
    ts_left = [f.name for f in ts.fields if f.name not in ignored]
    st_left = [f.name for f in st.fields]

    def take(t: str, s: str, basis: str, score: float = 1.0, candidates: tuple[str, ...] = ()) -> None:
        out.append(FieldMatch(t, s, basis, score, candidates))
        ts_left.remove(t)
        st_left.remove(s)

    if pkg.mapping:
        for e in pkg.mapping.fields:
            tm, _, tn = e.thoughtspot.rpartition("::")
            sm, _, sn = e.strategy.rpartition("::")
            if (not tm or tm == ts.name) and (not sm or sm == st.name) and tn in ts_left and sn in st_left:
                take(tn, sn, "mapped")

    synonyms = {f.name: {norm(f.name), *(norm(s) for s in f.synonyms)} for f in ts.fields}
    for t in list(ts_left):
        hit = next((s for s in st_left if norm(s) == norm(t)), None) \
            or next((s for s in st_left if norm(s) in synonyms.get(t, set())), None)
        if hit:
            take(t, hit, "exact")

    scored = sorted(((_score(ts_res[t], st_res[s], t, s), t, s) for t in ts_left for s in st_left), reverse=True)
    for score, t, s in scored:
        if score < THRESHOLD or t not in ts_left or s not in st_left:
            continue
        rivals = [x for x, tt, ss in scored if tt == t and ss != s and ss in st_left and score - x < AMBIGUITY_MARGIN]
        if rivals:
            cands = tuple(ss for x, tt, ss in scored if tt == t and ss in st_left and score - x < AMBIGUITY_MARGIN)
            out.append(FieldMatch(t, None, "ambiguous", score, cands))
            ts_left.remove(t)
            continue
        take(t, s, "inferred", round(score, 3))

    out.extend(FieldMatch(t, None, None, 0.0) for t in ts_left)
    out.extend(FieldMatch(None, s, None, 0.0) for s in st_left)
    return out
