"""Discovery CLI for .mstr files: the commands used to find every rule in decode.py (see README.md, playbook).

python -m strategy_agent.tools.mstr_reader.explore info    FILE.mstr
python -m strategy_agent.tools.mstr_reader.explore objects FILE.mstr [--type N]
python -m strategy_agent.tools.mstr_reader.explore where   FILE.mstr TEXT_OR_GUID [--max N]
python -m strategy_agent.tools.mstr_reader.explore object  FILE.mstr GUID [--lines N]
python -m strategy_agent.tools.mstr_reader.explore dump    FILE.mstr OFFSET [LENGTH]
python -m strategy_agent.tools.mstr_reader.explore compare A.mstr B.mstr
"""

from __future__ import annotations

import argparse
import io
from collections import Counter

from .decode import FX, OBJECT_TYPES, embedded_objects, envelope_objects
from .mstrfile import Doc, dump, guids, read_mstr, strings

PROP_NAME_TAG = 0x15  # property names are written 15 08 <len> 08 <~len> name


def _load(path):
    header, delta, members = read_mstr(path)
    return header, Doc(delta), members


def _chain_text(d: Doc, pos: int, depth: int = 8) -> str:
    return " < ".join(f"{d.bl[i][2]:04x}:{d.head(i, 4).hex()}@{d.bl[i][0]}" for i in d.chain(pos)[:depth])


def _property_names(d: Doc) -> Counter:
    return Counter(s for p, s in strings(d.b) if p >= 10 and d.b[p - 10] == PROP_NAME_TAG)


def cmd_info(a):
    header, d, members = _load(a.file)
    print("header:", header)
    print("members:", *members, sep="\n  ")
    print(f".delta: {len(d.b)} bytes, {len(d.bl)} blocks, {len(guids(d.b))} guids, {len(strings(d.b))} strings")
    print("block types:", [(f"{t:04x}", n) for t, n in Counter(x[2] for x in d.bl).most_common(25)])
    objs = {**envelope_objects(d), **embedded_objects(d)}
    print("object types:", [(t, OBJECT_TYPES.get(t, "?"), n) for t, n in Counter(o["type"] for o in objs.values()).most_common()])
    unknown = Counter(g for _, g in guids(d.b) if g not in objs and g not in FX and g != "0" * 32)
    print(f"guids that are neither objects nor known functions: {len(unknown)} distinct")


def cmd_objects(a):
    _h, d, _m = _load(a.file)
    env, emb = envelope_objects(d), embedded_objects(d)
    for g, o in {**env, **emb}.items():
        if a.type is None or o["type"] == a.type:
            where = "envelope" if g in env else "embedded"
            print(f"{g}  type {o['type']:>3} {OBJECT_TYPES.get(o['type'], '?'):<20} {where:<9} @{d.bl[o['block']][0]:<9} {o['name']!r}")


def cmd_where(a):
    _h, d, _m = _load(a.file)
    needle = a.needle
    is_guid = len(needle) == 32 and all(c in "0123456789ABCDEF" for c in needle.upper())
    hits = [p for p, g in guids(d.b) if g == needle.upper()] if is_guid else [p for p, s in strings(d.b) if s == needle]
    print(f"{len(hits)} hits")
    for p in hits[:a.max]:
        print(f"@{p}  {_chain_text(d, p)}")
        i = d.enclosing(p)
        if i is not None:
            print("    own strings:", [s for _, s in d.own_strings(i)][:12])
            print("    guids:", [(g[:8] + ("=" + FX[g] if g in FX else "")) for _, g in d.guids_in(i)][:12])


def cmd_object(a):
    _h, d, _m = _load(a.file)
    objs = {**envelope_objects(d), **embedded_objects(d)}
    o = objs.get(a.guid.upper())
    if not o:
        print("not an object in this file; try `where`")
        return
    s, e, _t, _c = d.bl[o["block"]]
    buf = io.StringIO()
    dump(d.b, s, e, 0, out=lambda x: buf.write(x + "\n"))
    lines = [ln for ln in buf.getvalue().splitlines() if not ln.strip().startswith("raw 00")]
    named = []
    for ln in lines[:a.lines]:
        st = ln.strip()
        if st.startswith("guid "):
            g = st[5:]
            ln += f"    = fn {FX[g]}" if g in FX else (f"    = {objs[g]['name']!r} (type {objs[g]['type']})" if g in objs else "")
        named.append(ln)
    print("\n".join(named))


def cmd_dump(a):
    _h, d, _m = _load(a.file)
    k = d.block_at(a.offset)
    end = a.offset + a.length if a.length else (d.bl[k][1] if k is not None else a.offset + 256)
    dump(d.b, a.offset, end)


def cmd_compare(a):
    """What exists in B but not in A (block types, property names, object types): where to look in a new version."""
    (ha, da, _), (hb, db, _) = _load(a.a), _load(a.b)
    print("A:", ha.get("MSTRVer"), ha.get("AEVersion"), " B:", hb.get("MSTRVer"), hb.get("AEVersion"))
    ta, tb = Counter(x[2] for x in da.bl), Counter(x[2] for x in db.bl)
    print("block types only in B:", sorted(f"{t:04x}" for t in tb.keys() - ta.keys()))
    pa, pb = _property_names(da), _property_names(db)
    print("property names only in B:", sorted(pb.keys() - pa.keys()))
    oa = Counter(o["type"] for o in {**envelope_objects(da), **embedded_objects(da)}.values())
    ob = Counter(o["type"] for o in {**envelope_objects(db), **embedded_objects(db)}.values())
    print("object types only in B:", sorted(ob.keys() - oa.keys()))


def main():
    ap = argparse.ArgumentParser(prog="mstr-explore")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info"); p.add_argument("file"); p.set_defaults(fn=cmd_info)
    p = sub.add_parser("objects"); p.add_argument("file"); p.add_argument("--type", type=int); p.set_defaults(fn=cmd_objects)
    p = sub.add_parser("where"); p.add_argument("file"); p.add_argument("needle"); p.add_argument("--max", type=int, default=5)
    p.set_defaults(fn=cmd_where)
    p = sub.add_parser("object"); p.add_argument("file"); p.add_argument("guid"); p.add_argument("--lines", type=int, default=200)
    p.set_defaults(fn=cmd_object)
    p = sub.add_parser("dump"); p.add_argument("file"); p.add_argument("offset", type=int); p.add_argument("length", type=int, nargs="?")
    p.set_defaults(fn=cmd_dump)
    p = sub.add_parser("compare"); p.add_argument("a"); p.add_argument("b"); p.set_defaults(fn=cmd_compare)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
