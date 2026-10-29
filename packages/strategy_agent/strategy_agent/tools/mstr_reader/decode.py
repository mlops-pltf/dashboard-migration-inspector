"""Semantic layer: .mstr -> dashboard structure (visualizations, units, sorts, limits, filters, derived metrics).

Every rule here was found on real .mstr files and checked against Strategy's REST answer for the same
dashboard (README.md, "How each rule was found"). Anything not decoded is reported in `issues`, never guessed.

usage: python -m strategy_agent.tools.mstr_reader.decode FILE.mstr [--json]
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

from .mstrfile import NULL, TESTED_VERSIONS, Doc, guid_at, guids, is_value_string, read_mstr, strings

FX: dict[str, str] = json.loads((Path(__file__).parent / "functions.json").read_text())

OBJECT_TYPES = {1: "filter", 2: "template", 3: "report/cube", 4: "metric", 7: "aggregate metric", 8: "folder",
                10: "prompt", 11: "function", 12: "attribute", 13: "fact", 14: "hierarchy", 15: "table",
                21: "attribute form", 26: "column", 47: "consolidation", 53: "db table", 55: "document/dashboard",
                61: "(layout object)", 63: "image", 71: "(theme)"}
PROPERTY_NOISE = {"SubBlockDirection", "SubBlockSize", "FunctionType", "InputText", "TokenStream", "Stating",
                  "WatchFlag", "TableGuide", "JointElementEnhancement", "ScriptFile", ":"}
COMPARE = {"<=", ">", "<", ">=", "=", "<>", "Between", "NotBetween", "In", "NotIn"}
VIZ_KEY = re.compile(r"W\d+|K\d+|(?:W|K|IGK)?[0-9A-Fa-f]{32}")
SELECTOR_KEY = re.compile(r"W\d+|IGK\d+|(?:IGK|W|K)?[0-9A-F]{32}")
UNIT_KIND = {0x00: "attribute", 0x04: "templateMetrics", 0x08: "metric"}


# ---------------------------------------------------------------- objects

def embedded_objects(d: Doc) -> dict[str, dict]:
    """00ae blocks: guid, i32 object type, 0002 child with the name. Derived metrics, templates, filters, ..."""
    out = {}
    for i, (_s, e, t, c) in enumerate(d.bl):
        if t != 0x00AE:
            continue
        gid = guid_at(d.b, c)
        if not gid:
            continue
        otype = struct.unpack_from("<i", d.b, c + 21)[0] if d.b[c + 20] == 0x06 else None
        name = next((d.strings_in(k)[0][1] for k in d.kids[i] if d.bl[k][2] == 0x0002 and d.strings_in(k)), "")
        out.setdefault(gid, {"id": gid, "type": otype, "name": name, "block": i})
    return out


def envelope_objects(d: Doc) -> dict[str, dict]:
    """00ef envelope > 00ed header (06 ver, guid id, 06 type, guid project) + 00ef body > 0002 (name).
    These are full metadata copies of the dashboard's dependencies: dataset, attributes, metrics, tables, columns."""
    out = {}
    for i, (_s, _e, t, c) in enumerate(d.bl):
        if t != 0x00ED or d.par[i] is None or d.bl[d.par[i]][2] != 0x00EF or d.b[c] != 0x06:
            continue
        gid = guid_at(d.b, c + 5)
        if not gid or d.b[c + 25] != 0x06:
            continue
        env = d.par[i]
        body = next((k for k in d.kids[env] if d.bl[k][2] == 0x00EF), None)
        name = ""
        if body is not None:
            for k in d.kids[body]:
                if d.bl[k][2] == 0x0002:
                    name = next((x for _, x in d.strings_in(k) if x), "")
                    break
        out.setdefault(gid, {"id": gid, "type": struct.unpack_from("<i", d.b, c + 26)[0], "name": name,
                             "block": env})
    return out


def dataset_aliases(d: Doc) -> dict[str, str]:
    """Dashboard-level renames of dataset objects: 0000 blocks '12 00 06 <n> 06 <locale> 01 08 <name> .. guid 12 <type>'."""
    out = {}
    for _i, (_s, e, t, c) in enumerate(d.bl):
        if t != 0 or e - c > 400 or d.b[c:c + 3] != b"\x12\x00\x06" or d.b[c + 7] != 0x06 or d.b[c + 12] != 0x01:
            continue
        st = [x for _, x in strings(d.b, c, e) if x]
        gs = [g for _, g in d.guids_in(_i) if g != NULL]
        if len(st) == 1 and len(gs) == 1:
            out.setdefault(gs[0], st[0])
    return out


# ---------------------------------------------------------------- expressions

class _Cursor:
    """Sequential reader inside a block that steps over child blocks."""

    def __init__(self, d: Doc, lo: int, hi: int):
        self.d, self.p, self.hi = d, lo, hi

    def skip_to_block(self) -> int | None:
        while self.p < self.hi and self.d.block_at(self.p) is None:
            self.p += 1
        return self.d.block_at(self.p)

    def i32(self) -> int | None:
        b = self.d.b
        while self.p < self.hi and b[self.p] != 0x06:
            k = self.d.block_at(self.p)
            self.p = self.d.bl[k][1] if k is not None else self.p + 1
        if self.p >= self.hi:
            return None
        v = struct.unpack_from("<i", b, self.p + 1)[0]
        self.p += 5
        return v


def _inner(d: Doc, i: int) -> int:
    """Unwrap x08x wrapper blocks down to their 0000 content block."""
    while d.kids[i] and d.bl[i][2] & 0x0FF0 == 0x0080 and d.bl[d.kids[i][0]][2] == 0:
        i = d.kids[i][0]
    return i


def _constant(b: bytes, lo: int, hi: int):
    """Constant node: 12 <flag> 06 <i32> 05 <VARTYPE> <value>; value tag 06 int, 02 double, 08 string, 01 08.. string."""
    m = re.compile(rb"\x12[\x00-\x01]\x06.{4}\x05", re.S).search(b, lo, hi)
    if not m:
        return None
    p = m.end() - 1
    vt = struct.unpack_from("<h", b, p + 1)[0]
    p += 3
    tag = b[p]
    if tag == 0x06:
        return struct.unpack_from("<i", b, p + 1)[0]
    if tag == 0x02:
        return struct.unpack_from("<d", b, p + 1)[0]
    if tag == 0x05:
        return struct.unpack_from("<h", b, p + 1)[0]
    if tag == 0x08:
        n = struct.unpack_from("<q", b, p + 1)[0]
        if n < 0:
            return b[p + 9:p + 9 + ~n - 1].decode("utf-8", "replace")
    if tag == 0x01 and b[p + 1] == 0x08 and b[p + 10] == 0x08:
        n = struct.unpack_from("<q", b, p + 11)[0]
        if n < 0:
            return b[p + 19:p + 19 + ~n - 1].decode("utf-8", "replace")
    return {"vartype": vt, "hex": b[p:p + 12].hex(" ")}


def expr_node(d: Doc, ntype: int, blk: int, depth: int = 0) -> dict:
    """Node types: 1 attribute(-form) ref, 2 element list, 3 constant, 4 operator, 5 object ref, 6 (date/system, open)."""
    if depth > 30:
        return {"node": ntype, "error": "too deep"}
    i = _inner(d, blk)
    _s, e, _t, c = d.bl[i]
    if ntype == 5:
        for p, g in d.guids_in(i):
            if g != NULL:
                return {"ref": g, "objectType": d.b[p + 21] if d.b[p + 20] == 0x12 else None}
        return {"ref": None}
    if ntype == 4:
        lst = next((k for k in d.kids[i] if d.bl[k][2] & 0x0FFF == 0x0081), None)
        args, fn_pos = [], c
        if lst is not None:
            li = _inner(d, lst)
            cur = _Cursor(d, d.bl[li][3], d.bl[li][1])
            for _ in range(cur.i32() or 0):
                t, k = cur.i32(), cur.skip_to_block()
                if t is None or k is None:
                    break
                args.append(expr_node(d, t, k, depth + 1))
                cur.p = d.bl[k][1]
            fn_pos = d.bl[lst][1]
        fn = next((g for _, g in guids(d.b, fn_pos, e) if g != NULL), None)
        return {"fn": FX.get(fn, fn), "args": args}
    if ntype == 3:
        v = _constant(d.b, c, e)
        if v is not None:
            return {"const": v}
    if ntype == 2:
        vals = []
        for _, x in strings(d.b, c, e):
            v = x.split(";")[0]
            v = v[1:] if v[:1] == "h" else v  # element ids look like "h<value>;<attribute guid>"
            if x and v and v not in vals and x not in PROPERTY_NOISE:
                vals.append(v)
        return {"elements": vals}
    if ntype == 6:  # date-time constant: ... 0a 06 <year> 06 <month> 06 <day> 06 <h> 06 <m> 06 <s>
        k = d.b.find(b"\x0a\x06", c, e)
        if k >= 0:
            y, mo, dd, hh, mi, ss = d.i32s(k + 1, e, 6) + [0] * max(0, 6 - len(d.i32s(k + 1, e, 6)))
            if 1 <= mo <= 12 and 1 <= dd <= 31:
                return {"const": f"{y:04d}-{mo:02d}-{dd:02d}" + (f" {hh:02d}:{mi:02d}:{ss:02d}" if hh or mi or ss else "")}
    if ntype in (1, 6):
        gs = [g for _, g in d.guids_in(i) if g != NULL]
        if gs:
            return {"ref": gs[0], "forms": gs[1:], "node": ntype}
    return {"node": ntype, "hex": d.b[c:min(e, c + 160)].hex(" ")}


def object_expression(d: Doc, objblock: int) -> dict | None:
    """Expression of a metric/filter object: 0006 > x08x > 0000 > first x081 = root (i32 node type + block)."""
    for k in d.kids[objblock]:
        if d.bl[k][2] == 0x0006 and d.kids[k]:
            top = _inner(d, d.kids[k][0])
            root = next((x for x in d.kids[top] if d.bl[x][2] & 0x0FFF == 0x0081), None)
            if root is None:
                return None
            ri = _inner(d, root)
            cur = _Cursor(d, d.bl[ri][3], d.bl[ri][1])
            t, k2 = cur.i32(), cur.skip_to_block()
            return expr_node(d, t, k2) if t is not None and k2 is not None else None
    return None


def render(n: dict | None, names: dict[str, str], inline: dict[str, dict] | None = None, _depth: int = 0) -> str:
    """Text form. Unnamed objects (e.g. Rank(...) helpers, aggregate metrics) are expanded in place via `inline`."""
    if n is None:
        return "?"
    if "ref" in n:
        if n["ref"] in names:
            return f"[{names[n['ref']]}]"
        if inline and n["ref"] in inline and _depth < 8:
            return render(inline[n["ref"]], names, inline, _depth + 1)
        return f"[{(n['ref'] or '?')[:8]}]"
    if "fn" in n:
        a = [render(x, names, inline, _depth) for x in n["args"]]
        f = n["fn"] or "?"
        if f in ("+", "-", "*", "/", "<", ">", "<=", ">=", "=", "<>") and len(a) == 2:
            return f"({a[0]} {f} {a[1]})"
        return f"{f}({', '.join(a)})"
    if "elements" in n:
        return "{" + ", ".join(n["elements"]) + "}"
    if "const" in n:
        v = n["const"]
        return repr(v) if isinstance(v, str) else f"{v:g}" if isinstance(v, (int, float)) else str(v)
    return f"<node{n.get('node')}?>"


# ---------------------------------------------------------------- layout and templates

def layout_visualizations(d: Doc, objs: dict[str, dict]) -> list[dict]:
    """Viz = 0000 block (parent x085), content starts 12 02, own strings: name, [subtitle..], source label, key.
    The template guid and dataset guid follow the end of an enclosing block; fallback: first template inside it."""
    out = []
    for i, (_s, _e, t, c) in enumerate(d.bl):
        if t != 0 or d.b[c:c + 2] != b"\x12\x02" or d.par[i] is None or d.bl[d.par[i]][2] & 0x0FFF != 0x0085:
            continue
        st = d.own_strings(i)
        k = next((n for n in range(2, min(len(st), 6)) if VIZ_KEY.fullmatch(st[n][1] or "")), None)
        if k is None:
            continue
        pos, name, key = st[k - 1][0], st[0][1], st[k][1]
        tmpl = ds = None
        for a in d.chain(pos):
            end = d.bl[a][1]
            nxt = guids(d.b, end, end + 60)
            if nxt and nxt[0][0] == end:
                tmpl, ds = nxt[0][1], (nxt[1][1] if len(nxt) > 1 else None)
                break
        if tmpl not in objs:
            inner = d.enclosing(pos)
            gs = [g for _, g in d.guids_in(inner)] if inner is not None else []
            for n, g in enumerate(gs):
                if g in objs and objs[g]["type"] == 2:
                    tmpl = g
                    ds = next((x for x in gs[n + 1:n + 2] if x != NULL), ds)
                    break
        out.append({"key": key, "name": name, "source_label": st[k - 1][1], "template": tmpl, "dataset": ds})
    return out


def _units(d: Doc, axis: int) -> list[dict]:
    """Units of an axis: blocks with parent x083 whose content starts 12 xx 12 <kind> (or 12 xx + block = attribute)."""
    heads, stack = [], [axis]
    while stack:
        i = stack.pop()
        h, p = d.head(i, 4), d.par[i]
        if (p is not None and d.bl[p][2] & 0x0FFF == 0x0083 and len(h) == 4 and h[0] == 0x12
                and ((h[2] == 0x12 and h[3] in UNIT_KIND) or h[2:4] == b"\x0e\x04")):
            heads.append(i)
            continue
        stack.extend(reversed(d.kids[i]))
    units = []
    for n, u in enumerate(heads):
        h = d.head(u, 4)
        hi = d.bl[heads[n + 1]][0] if n + 1 < len(heads) else d.bl[axis][1]
        kind = "attribute" if h[2] == 0x0E else UNIT_KIND[h[3]]
        if kind == "templateMetrics":
            continue  # the "Metrics" placeholder; its metrics follow as their own units
        gid = next((g for _, g in guids(d.b, d.bl[u][0], hi) if g != NULL), None)
        alias = None
        st = strings(d.b, d.bl[u][0], hi)
        for k, (_p, s) in enumerate(st):
            if s == "TokenStream":  # unit alias = the value string right after TokenStream's own value
                for p2, s2 in st[k + 2:k + 3]:
                    if s2 and is_value_string(d.b, p2):
                        alias = s2
                break
        units.append({"id": gid, "kind": kind, "alias": alias})
    return units


def template_axes(d: Doc, tblock: int) -> list[list[dict]]:
    """Axis blocks start 12 3c (then two counts). In template order: [rows, columns, ...]."""
    axes, stack = [], [tblock]
    while stack:
        i = stack.pop()
        if d.head(i, 2) == b"\x12\x3c":
            axes.append(_units(d, i))
            continue
        stack.extend(reversed(d.kids[i]))
    return axes


def template_sorts(d: Doc, tblock: int) -> list[dict]:
    """x084 blocks whose first child starts 12 <flags> 12 00 12 <kind>; kind bit 0x08 = ascending."""
    out, end = [], d.bl[tblock][1]
    for j in range(tblock, len(d.bl)):
        if d.bl[j][0] >= end:
            break
        if d.bl[j][2] & 0x0FFF != 0x0084 or not d.kids[j]:
            continue
        k = d.kids[j][0]
        h = d.head(k, 6)
        if len(h) < 6 or h[0] != 0x12 or h[2:5] != b"\x12\x00\x12":
            continue
        ids = [g for _, g in guids(d.b, d.bl[k][3], d.bl[k][1]) if g != NULL]
        out.append({"ids": ids, "order": "ascending" if h[5] & 0x08 else "descending"})
    return out


def template_conditions(d: Doc, tblock: int) -> tuple[list[dict], list[dict]]:
    """Comparisons in a template. Inside a 12 10 container = threshold (formatting); directly under a unit = limit."""
    limits, thresholds, seen = [], [], set()
    for p, g in guids(d.b, d.bl[tblock][3], d.bl[tblock][1]):
        if FX.get(g) not in COMPARE:
            continue
        i = d.enclosing(p)
        if i is None or i in seen:
            continue
        seen.add(i)
        kind, j = "limit", d.par[i]
        while j is not None and j != tblock:
            h = d.head(j, 3)
            if h[:2] == b"\x12\x10":
                kind = "threshold"
                break
            if h[:1] == b"\x12" and len(h) > 2 and h[2] == 0x12:
                break
            j = d.par[j]
        (limits if kind == "limit" else thresholds).append(expr_node(d, 4, i))
    return limits, thresholds


def selectors(d: Doc) -> list[dict]:
    """Filter/selector controls: x089 blocks. Own strings: [selected element pairs..], name, key; first guid = source."""
    out = []
    for i, (_s, _e, t, _c) in enumerate(d.bl):
        if t & 0x0FFF != 0x0089:
            continue
        st = [x for _, x in d.strings_in(i) if x and x not in PROPERTY_NOISE]
        k = next((n for n, x in enumerate(st) if SELECTOR_KEY.fullmatch(x)), None)
        if k is None:
            continue
        elements = []
        for x in st[:max(0, k - 1)]:
            if not elements or elements[-1] != x:  # display pairs are written twice
                elements.append(x)
        gs = [g for _, g in d.guids_in(i) if g != NULL]
        out.append({"key": st[k], "name": st[k - 1] if k > 0 else "", "source": gs[0] if gs else None,
                    "selected": elements, "functions": sorted({FX[g] for g in gs if g in FX})})
    return out


# ---------------------------------------------------------------- whole file

def decode_file(path: str | Path) -> dict:
    header, delta, members = read_mstr(path)
    d = Doc(delta)
    embedded, env = embedded_objects(d), envelope_objects(d)
    objs = {**env, **embedded}
    names = {g: o["name"] for g, o in objs.items() if o["name"]}
    inline = {g: object_expression(d, o["block"]) for g, o in embedded.items()
              if not o["name"] and o["type"] in (4, 7)}
    ds_alias = dataset_aliases(d)
    issues: list[dict] = []

    def text(node):
        s = render(node, names, inline)
        if "<node" in s:
            issues.append({"kind": "unknown expression node", "text": s})
        if re.search(r"\b[0-9A-F]{32}\(", s):
            issues.append({"kind": "unknown function id", "text": s})
        return s

    if (header.get("MSTRVer"), header.get("AEVersion")) not in TESTED_VERSIONS:
        issues.append({"kind": "untested writer version", "MSTRVer": header.get("MSTRVer"),
                       "AEVersion": header.get("AEVersion")})

    def unit(u):
        return {**u, "name": names.get(u["id"]), "alias": u["alias"] or ds_alias.get(u["id"])}

    vizzes = []
    for v in layout_visualizations(d, embedded):
        t = embedded.get(v["template"])
        if not t:
            issues.append({"kind": "layout item without template (text/image widget, or a visualization not decoded)",
                           "key": v["key"], "name": v["name"]})
            vizzes.append({**v, "decoded": False})
            continue
        tb = t["block"]
        axes = template_axes(d, tb)
        limits, thresholds = template_conditions(d, tb)
        refs = dict.fromkeys(g for _, g in d.guids_in(tb))
        view_trees = [object_expression(d, embedded[g]["block"]) for g in refs
                      if g in embedded and embedded[g]["type"] == 1]
        view_trees = [n for n in view_trees if n and not (n.get("fn") == "And" and not n["args"])]
        vizzes.append({
            **v, "decoded": True, "dataset_name": names.get(v["dataset"]),
            "rows": [unit(u) for u in (axes[0] if axes else [])],
            "columns": [unit(u) for u in (axes[1] if len(axes) > 1 else [])],
            "other_axes": [[unit(u) for u in ax] for ax in axes[2:]],
            "sorts": [{"by": [names.get(g, g) for g in s["ids"]], "order": s["order"]}
                      for s in template_sorts(d, tb)],
            "limits": [text(n) for n in limits if n.get("fn") not in ("In", "NotIn")],
            "limit_trees": [n for n in limits if n.get("fn") not in ("In", "NotIn")],
            "element_conditions": [text(n) for n in limits if n.get("fn") in ("In", "NotIn")],
            "thresholds": len(thresholds),
            "view_filter": [text(n) for n in view_trees],
            "view_filter_trees": view_trees,
        })

    filters = [{**s, "source_name": names.get(s["source"])} for s in selectors(d)]
    derived = []
    for g, o in embedded.items():
        if o["type"] == 4 and o["name"]:
            tree = object_expression(d, o["block"])
            derived.append({"id": g, "name": o["name"], "expression": text(tree), "tree": tree})
    dash = next((o for o in env.values() if o["type"] == 55), {})
    return {
        "header": header,
        "dashboard": {"id": header.get("dashboardId"), "name": dash.get("name")},
        "datasets": [{"id": g, "name": o["name"]} for g, o in env.items() if o["type"] == 3],
        "objects": {g: {"type": o["type"], "kind": OBJECT_TYPES.get(o["type"], "?"), "name": o["name"]}
                    for g, o in objs.items()},
        "visualizations": vizzes,
        "filters": filters,
        "derived_metrics": derived,
        "helpers": inline,  # unnamed metrics (Rank/aggregation helpers): guid -> expression tree
        "issues": issues,
        "coverage": {
            "visualizations": len(vizzes),
            "visualizations_decoded": sum(v["decoded"] for v in vizzes),
            "derived_metrics": len(derived),
            "derived_metrics_fully_decoded": sum("<node" not in m["expression"] for m in derived),
            "filters": len(filters),
            "cube_members_not_decoded": sum(m.endswith(".cube") for m in members),
        },
    }


def summary(r: dict) -> str:
    lines = [f"{r['dashboard']['name']}  (MSTRVer {r['header'].get('MSTRVer')}, AEVersion {r['header'].get('AEVersion')})",
             f"datasets: {', '.join(x['name'] for x in r['datasets'])}", f"coverage: {json.dumps(r['coverage'])}"]
    for v in r["visualizations"]:
        lines.append(f"\n[{v['key']}] {v['name']}" + ("" if v["decoded"] else "  (NOT DECODED)"))
        if not v["decoded"]:
            continue
        for label in ("rows", "columns"):
            lines.append(f"  {label}: " + ", ".join(
                (u["alias"] or u["name"] or u["id"][:8]) + (f" ({u['name']})" if u["alias"] and u["name"] and u["alias"] != u["name"] else "")
                for u in v[label]))
        for k in ("sorts", "limits", "view_filter", "element_conditions"):
            if v[k]:
                lines.append(f"  {k}: {v[k]}")
    lines.append("\nfilters:")
    for f in r["filters"]:
        lines.append(f"  [{f['key']}] {f['name']} on {f['source_name']}: {f['selected'] or f['functions'] or 'all'}")
    lines.append("\nderived metrics:")
    lines += [f"  {m['name']} = {m['expression']}" for m in r["derived_metrics"]]
    if r["issues"]:
        lines.append("\nissues:")
        lines += [f"  {json.dumps(i)}" for i in r["issues"]]
    return "\n".join(lines)


if __name__ == "__main__":
    res = decode_file(sys.argv[1])
    print(json.dumps(res, indent=1, default=str) if "--json" in sys.argv else summary(res))
