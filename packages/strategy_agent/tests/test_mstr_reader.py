""".mstr reader on a synthetic file written with the documented encoding (README.md, sections 2-4)."""

import struct
import zipfile

from strategy_agent.tools.mstr_reader.decode import decode_file
from strategy_agent.tools.mstr_reader.mstrfile import Doc, guid_at, strings

DIVIDE = "8107C313DD9911D3B98100C04F2233EA"  # "/" in functions.json
PROJECT, DASH = "EC70648611E7A2F962E90080EFD58751", "F6A70DCFBA4B3389BAE622A93A6CBE90"
REVENUE, DERIVED = "2E333D0B4646EC513978F7A589BF8197", "0A0F85835E4B9DEAE736D48DA962F663"


def blk(t: int, *parts: bytes) -> bytes:
    body = b"".join(parts)
    return b"\x0e\x04" + struct.pack("<H", t) + b"\x08" + struct.pack("<q", len(body)) + b"\x06" + struct.pack("<i", ~len(body)) + body


def i32(v: int) -> bytes:
    return b"\x06" + struct.pack("<i", v)


def s(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return b"\x08" + struct.pack("<q", ~len(raw)) + raw


def g(h: str) -> bytes:
    r = bytes.fromhex(h)
    return (b"\x09\x07" + r[0:4][::-1] + b"\x05" + r[6:8][::-1] + b"\x05" + r[4:6][::-1]
            + r[8:12][::-1] + r[12:16][::-1])


def synthetic_mstr(path):
    revenue = blk(0x00EF, blk(0x00ED, i32(1), g(REVENUE), i32(4), g(PROJECT)),
                  blk(0x00EF, blk(0x0002, i32(1033), b"\x01" + s("Revenue"))))
    ref = blk(0xB082, blk(0, b"\x12\x00\x12\x00", g(REVENUE), b"\x12\x04"))
    const = blk(0xB082, blk(0, b"\x12\x01", i32(0), b"\x05\x03\x00", i32(100)))
    operator = blk(0xB083, blk(0, b"\x12\x00", blk(0xB081, blk(0, i32(2), i32(5), ref, i32(3), const)), g(DIVIDE)))
    derived = blk(0x00AE, g(DERIVED), i32(4), blk(0x0002, b"\x01" + s("Revenue per 100")),
                  blk(0x0006, blk(0xB083, blk(0, b"\x12\x30", blk(0xB081, blk(0, i32(4), operator))))))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{PROJECT}/{DASH}.delta", revenue + blk(0x00AD, i32(1), derived))
        z.comment = b"1;MSTRVer:6=110300;AEVersion:2=13"
    return path


def test_primitives():
    raw = blk(0x3081, g(REVENUE), s("hello"), blk(0, i32(7)))
    d = Doc(raw)
    assert [(t, d.par[i]) for i, (_s, _e, t, _c) in enumerate(d.bl)] == [(0x3081, None), (0, 0)]
    assert guid_at(raw, d.bl[0][3]) == REVENUE
    assert [x for _, x in strings(raw)] == ["hello"]


def test_derived_metric_expression(tmp_path):
    r = decode_file(synthetic_mstr(tmp_path / "x.mstr"))
    assert r["objects"][REVENUE]["name"] == "Revenue"
    assert [(m["id"], m["name"], m["expression"]) for m in r["derived_metrics"]] == [
        (DERIVED, "Revenue per 100", "([Revenue] / 100)")]
    assert r["issues"] == []


def test_dashboard_ir_links_model_fields_by_object_id(tmp_path):
    from inspector_contract import BinOp, Col, Field, Lit, Ref, SemanticModel, SourceRef
    from strategy_agent.tools.mstr_reader.to_ir import load

    path = synthetic_mstr(tmp_path / "x.mstr")
    dash, issues = load(path)
    (field,) = dash.fields
    assert field.expr == BinOp(op="/", left=Ref(name="Revenue"), right=Lit(value=100, type="number"))
    assert issues == []

    model = SemanticModel(id="mstr:sales", name="Sales", tool="strategy", kind="mosaic", fields=(
        Field(id="mstr:sales:Net Revenue", name="Net Revenue", kind="MEASURE", expr=Col(table="t", column="amount"),
              object_type="mstr:metric", source=SourceRef(file="model.yaml", object_id=REVENUE)),))
    dash, issues = load(path, (model,))
    assert dash.fields[0].expr.left == Ref(name="Net Revenue")  # the model's name wins: same object id
    assert issues == []
