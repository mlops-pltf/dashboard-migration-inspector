"""Strategy dashboard as a binary .mstr, the format the project actually delivers.

Written with exactly the encoding `strategy_agent/tools/mstr_reader` decodes (see its README.md): object
envelopes for the dataset and every attribute/metric used (same object ids as the model export, so the
.mstr <-> model join is exercised), one template per visualization (rows, columns, sort, top-N as
Rank(metric) <= N), layout items linking visualization keys to templates, and element-list selectors.
Chart types are not written: the reader does not decode them yet.
"""

from __future__ import annotations

import io
import struct
import zipfile

from .domain import Liveboard
from .ids import strategy_id

PROJECT = strategy_id("project", "Retail")
NULL = "0" * 32
RANK, LE = "8107C326DD9911D3B98100C04F2233EA", "8107C31ADD9911D3B98100C04F2233EA"
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def _blk(t: int, *parts: bytes) -> bytes:
    body = b"".join(parts)
    return (b"\x0e\x04" + struct.pack("<H", t) + b"\x08" + struct.pack("<q", len(body))
            + b"\x06" + struct.pack("<i", ~len(body)) + body)


def _i32(v: int) -> bytes:
    return b"\x06" + struct.pack("<i", v)


def _str(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return b"\x08" + struct.pack("<q", ~len(raw)) + raw


def _val(text: str) -> bytes:
    """Property value: 01 08 <len> then the string."""
    return b"\x01\x08" + struct.pack("<q", len(text.encode()) + 1) + _str(text)


def _guid(h: str) -> bytes:
    r = bytes.fromhex(h)
    return (b"\x09\x07" + r[0:4][::-1] + b"\x05" + r[6:8][::-1] + b"\x05" + r[4:6][::-1]
            + r[8:12][::-1] + r[12:16][::-1])


def _envelope(oid: str, otype: int, name: str) -> bytes:
    return _blk(0x00EF, _blk(0x00ED, _i32(1), _guid(oid), _i32(otype), _guid(PROJECT)),
                _blk(0x00EF, _blk(0x0002, _i32(1033), _val(name))))


def _ref(oid: str, otype: int) -> bytes:
    return _blk(0xB082, _blk(0, b"\x12\x00\x12\x00", _guid(oid), bytes([0x12, otype])))


def _const(n: int) -> bytes:
    return _blk(0xB082, _blk(0, b"\x12\x01", _i32(0), b"\x05\x03\x00", _i32(n)))


def _operator(fn: str, *args: tuple[int, bytes]) -> bytes:
    """0000 content of an operator node: 12 00, child list (count, (type, block)*), function guid."""
    children = b"".join(_i32(t) + b for t, b in args)
    return _blk(0, b"\x12\x00", _blk(0xB081, _blk(0, _i32(len(args)), children)), _guid(fn))


def _rank_helper(hid: str, metric: str) -> bytes:
    """Unnamed metric Rank([metric]) behind a top-N limit."""
    rank = _blk(0xB083, _operator(RANK, (5, _ref(metric, 4))))
    return _blk(0x00AE, _guid(hid), _i32(4), _blk(0x0002, _i32(0)),
                _blk(0x0006, _blk(0xB083, _blk(0, b"\x12\x30", _blk(0xB081, _blk(0, _i32(4), rank))))))


def _template(tid: str, dims: list[str], measures: list[str], sort: tuple[str, bool] | None,
              limit: tuple[str, str, int] | None) -> bytes:
    """rows axis (attributes), columns axis (Metrics placeholder + metrics), sort, Rank(metric) <= N limit."""
    rows = b"".join(_blk(0xB083, _blk(0, b"\x12\x00\x12\x00", _i32(0))) + _guid(a) for a in dims)
    cols = _blk(0xB083, _blk(0, b"\x12\x00\x12\x04", _i32(0)))
    for m in measures:
        extra = b""
        if limit and limit[0] == m:
            extra = _blk(0xB081, _operator(LE, (5, _ref(limit[1], 4)), (3, _const(limit[2]))))
        cols += _blk(0xB083, _blk(0, b"\x12\x21\x12\x08", _guid(m), extra))
    sorts = b""
    if sort:
        sorts = _blk(0xB084, _blk(0, b"\x12\x40\x12\x00\x12" + bytes([0x2C if sort[1] else 0x24]),
                                  _guid(NULL), _guid(sort[0])))
    axis = lambda units, n: _blk(0xB081, _blk(0, b"\x12\x3c", _i32(0), _i32(n), _blk(0x3081, b"\x12\x01", units)))  # noqa: E731
    body = _blk(0xB085, _blk(0, b"\x12\x40\x12\x00", axis(rows, len(dims)), axis(cols, len(measures)), sorts))
    return _blk(0x00AE, _guid(tid), _i32(2), _blk(0x0002, _val("Local Template")), _blk(0x0006, body))


def mstr_bytes(lb: Liveboard, dashboard_id: str, dataset: tuple[str, str], ids: dict[str, tuple[str, int, str]]) -> bytes:
    """ids: ThoughtSpot field name -> (Strategy object id, object type 12 attribute / 4 metric, Strategy name)."""
    used = {n for v in lb.visualizations for n in (*v.dims, *v.measures)} | {f.field for f in lb.filters}
    used |= {v.sort[0] for v in lb.visualizations if v.sort}
    parts = [_envelope(dashboard_id, 55, lb.name), _envelope(dataset[0], 3, dataset[1])]
    parts += [_envelope(*ids[n]) for n in sorted(used)]

    objects, layout = [], []
    for i, v in enumerate(lb.visualizations, 1):
        tid = strategy_id(dashboard_id, "template", v.id)
        sort = (ids[v.sort[0]][0], v.sort[1] == "ASC") if v.sort else None
        limit = None
        if v.top_n:
            by = v.sort[0] if v.sort and v.sort[0] in v.measures else v.measures[0]
            hid = strategy_id(dashboard_id, "rank", v.id)
            objects.append(_rank_helper(hid, ids[by][0]))
            limit = (ids[by][0], hid, v.top_n)
        objects.append(_template(tid, [ids[d][0] for d in v.dims], [ids[m][0] for m in v.measures], sort, limit))
        item = _blk(0xB085, _blk(0, b"\x12\x02", _i32(2), _val(v.title or v.name), _val("SourceGrid"), _val(f"W{i}")))
        layout.append(_blk(0xB083, item, _guid(tid), _guid(dataset[0])))
    selectors = []
    for i, f in enumerate(lb.filters, 1):
        elements = b"".join(_val(x) + _val(x) for x in f.values)  # selected elements are written twice
        selectors.append(_blk(0xB089, _blk(0, elements, _val(ids[f.field][2]), _val(f"W{1000 + i}"), _guid(ids[f.field][0]))))
    delta = b"".join(parts) + _blk(0x00AD, _i32(len(objects)), *objects) + b"".join(layout) + b"".join(selectors)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo(f"{PROJECT}/{dashboard_id}.delta", ZIP_DATE), delta)
        z.comment = b"1;MSTRVer:6=110300;ClientStartVer:6=110300;MSTRFileType:1=0;AEVersion:2=13"
    return buf.getvalue()
