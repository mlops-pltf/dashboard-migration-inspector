"""Format layer for Strategy .mstr files: container, block tree, primitive values, navigation, hex-level dump.

A .mstr is a zip. Its comment holds the writer version (MSTRVer, AEVersion). Members:
    <projectId>/<dashboardId>.delta              dashboard definition + embedded metadata objects
    <projectId>/<datasetId>/<cubeId>.cube        cube data snapshot (not decoded)
    <projectId>/<datasetId>/<cubeId>_info.cube   cube header (not decoded)

The .delta is a tagged little-endian stream (see README.md for the full table):
    0e 04 <u16 type> 08 <i64 len> 06 <i32 ~len>   block header; `len` content bytes follow
    08 <i64 n>                                     int64; when n < 0 a UTF-8 string of ~n bytes (NUL included)
    06 <i32>  05 <i16>  02 <f64>  12 <u8>  01 ..   int32, int16, double, small enum, value-string prefix
    09 07 <4> 05 <2> 05 <2> <8>                    GUID (see guid_at)
"""

from __future__ import annotations

import bisect
import re
import struct
import zipfile
from pathlib import Path

NULL = "0" * 32
TESTED_VERSIONS = {("110300", "13")}  # (MSTRVer, AEVersion) the decoder was checked against

_HDR = re.compile(rb"\x0e\x04(..)\x08(.{8})\x06(.{4})", re.S)
_GUID = re.compile(rb"\x09\x07(.{4})\x05(..)\x05(..)(.{8})", re.S)
_STR = re.compile(rb"\x08(.{8})", re.S)


def read_mstr(path: str | Path) -> tuple[dict, bytes, list[str]]:
    """(header, .delta bytes, member names). Header keys come from the zip comment, e.g. MSTRVer, AEVersion."""
    with zipfile.ZipFile(path) as z:
        comment = z.comment.decode("latin-1")
        header = dict(re.findall(r"(\w+):\d+=([^;]*)", comment))
        names = z.namelist()
        delta = next((n for n in names if n.endswith(".delta")), None)
        if delta is None:
            raise ValueError(f"{path}: no .delta member (not a dashboard .mstr?)")
        header["dashboardId"] = Path(delta).stem
        header["projectId"] = delta.split("/")[0]
        return header, z.read(delta), names


def guid_at(b: bytes, i: int) -> str | None:
    """GUID at offset i: dword LE, two words (stored swapped), then 8 bytes as two LE dwords."""
    m = _GUID.match(b, i)
    if not m:
        return None
    d1, w1, w2, rest = m.groups()
    return (d1[::-1] + w2[::-1] + w1[::-1] + rest[0:4][::-1] + rest[4:8][::-1]).hex().upper()


def guids(b: bytes, lo: int = 0, hi: int | None = None) -> list[tuple[int, str]]:
    hi = len(b) if hi is None else hi
    return [(m.start(), guid_at(b, m.start())) for m in _GUID.finditer(b, lo, hi)]


def strings(b: bytes, lo: int = 0, hi: int | None = None) -> list[tuple[int, str]]:
    """(offset of the 08 tag, text) for every string token in [lo, hi)."""
    hi = len(b) if hi is None else hi
    out = []
    for m in _STR.finditer(b, lo, hi):
        n = struct.unpack("<q", m.group(1))[0]
        if n < 0 and 1 <= ~n <= 1_000_000 and m.end() + ~n <= hi and b[m.end() + ~n - 1] == 0:
            try:
                out.append((m.start(), b[m.end():m.end() + ~n - 1].decode("utf-8")))
            except UnicodeDecodeError:
                pass
    return out


def is_value_string(b: bytes, pos: int) -> bool:
    """Property values are written 01 08 <i64 len> 08 <~len> text; property names are 15 08 <len> 08 ..."""
    return pos >= 10 and b[pos - 10] == 0x01 and b[pos - 9] == 0x08


class Doc:
    """Block tree over a .delta. bl[i] = (start, end, type, content_start); par/kids give the nesting."""

    def __init__(self, b: bytes):
        self.b = b
        self.bl: list[tuple[int, int, int, int]] = []
        for m in _HDR.finditer(b):
            n = struct.unpack("<q", m.group(2))[0]
            if n >= 0 and struct.unpack("<i", m.group(3))[0] == ~n and m.end() + n <= len(b):
                self.bl.append((m.start(), m.end() + n, struct.unpack("<H", m.group(1))[0], m.end()))
        self.par: list[int | None] = []
        stack: list[int] = []
        for i, (s, _e, _t, _c) in enumerate(self.bl):
            while stack and self.bl[stack[-1]][1] <= s:
                stack.pop()
            self.par.append(stack[-1] if stack else None)
            stack.append(i)
        self.kids: list[list[int]] = [[] for _ in self.bl]
        for i, p in enumerate(self.par):
            if p is not None:
                self.kids[p].append(i)
        self.starts = [x[0] for x in self.bl]

    # navigation
    def head(self, i: int, n: int = 6) -> bytes:
        c = self.bl[i][3]
        return self.b[c:c + n]

    def block_at(self, pos: int) -> int | None:
        """Index of the block that starts exactly at pos."""
        i = bisect.bisect_left(self.starts, pos)
        return i if i < len(self.bl) and self.bl[i][0] == pos else None

    def enclosing(self, pos: int) -> int | None:
        """Innermost block containing pos."""
        i = bisect.bisect_right(self.starts, pos) - 1
        while i is not None and i >= 0 and self.bl[i][1] <= pos:
            i = self.par[i]
        return i if i is not None and i >= 0 else None

    def chain(self, pos: int) -> list[int]:
        out, i = [], self.enclosing(pos)
        while i is not None:
            out.append(i)
            i = self.par[i]
        return out

    def guids_in(self, i: int) -> list[tuple[int, str]]:
        return guids(self.b, self.bl[i][3], self.bl[i][1])

    def strings_in(self, i: int) -> list[tuple[int, str]]:
        return strings(self.b, self.bl[i][3], self.bl[i][1])

    def own_strings(self, i: int) -> list[tuple[int, str]]:
        """Strings directly in block i, not inside its child blocks."""
        kids = [(self.bl[k][0], self.bl[k][1]) for k in self.kids[i]]
        return [(p, s) for p, s in self.strings_in(i) if not any(a <= p < z for a, z in kids)]

    def i32s(self, lo: int, hi: int, limit: int = 32) -> list[int]:
        out, p = [], lo
        while p < hi and len(out) < limit:
            k = self.block_at(p)
            if k is not None:
                p = self.bl[k][1]
            elif self.b[p] == 0x06 and p + 5 <= hi:
                out.append(struct.unpack_from("<i", self.b, p + 1)[0])
                p += 5
            else:
                p += 1
        return out


def dump(b: bytes, lo: int, hi: int, depth: int = 0, out=print) -> None:
    """Pretty-print [lo, hi) as nested blocks and tokens; unknown bytes are shown as `raw`."""
    i, raw = lo, bytearray()

    def flush():
        if raw:
            out("  " * depth + "raw " + raw.hex(" "))
            raw.clear()

    while i < hi:
        t = b[i]
        if t == 0x0E and i + 18 <= hi and b[i + 1] == 4 and b[i + 4] == 8 and b[i + 13] == 6:
            n = struct.unpack_from("<q", b, i + 5)[0]
            if struct.unpack_from("<i", b, i + 14)[0] == ~n:
                flush()
                out("  " * depth + f"[{struct.unpack_from('<H', b, i + 2)[0]:04x}] @{i} len={n}")
                dump(b, i + 18, i + 18 + n, depth + 1, out)
                i += 18 + n
                continue
        if t == 0x09 and i + 20 <= hi and guid_at(b, i):
            flush()
            out("  " * depth + "guid " + guid_at(b, i))
            i += 20
            continue
        if t == 0x08 and i + 9 <= hi:
            n = struct.unpack_from("<q", b, i + 1)[0]
            if n < 0 and 1 <= ~n and i + 9 + ~n <= hi and b[i + 9 + ~n - 1] == 0:
                flush()
                out("  " * depth + "str " + repr(b[i + 9:i + 9 + ~n - 1].decode("utf-8", "replace")))
                i += 9 + ~n
                continue
            if 0 <= n < 1 << 40:
                flush()
                out("  " * depth + f"i64 {n}")
                i += 9
                continue
        if t == 0x06 and i + 5 <= hi:
            flush()
            out("  " * depth + f"i32 {struct.unpack_from('<i', b, i + 1)[0]}")
            i += 5
            continue
        if t == 0x05 and i + 3 <= hi:
            flush()
            out("  " * depth + f"i16 {struct.unpack_from('<h', b, i + 1)[0]}")
            i += 3
            continue
        raw.append(t)
        i += 1
    flush()
