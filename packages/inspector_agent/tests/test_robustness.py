"""Damaged or unexpected package files: the run never crashes, every unreadable file is named in a PK-01 lead,
and what could be read is still validated."""

import shutil
import zipfile

import pytest

from inspector_agent.inspector_agent import run_rules_only

from conftest import PACKAGES, needs_synthetic, pkg

SID = "S01"


def _first(root, pattern):
    return sorted(root.rglob(pattern))[0]


def _flip_delta(f):
    with zipfile.ZipFile(f) as z:
        name = next(n for n in z.namelist() if n.endswith(".delta"))
        data, comment = bytearray(z.read(name)), z.comment
    for i in range(100, len(data), 97):
        data[i] ^= 0x5A
    with zipfile.ZipFile(f, "w") as w:
        w.writestr(name, bytes(data))
        w.comment = comment


# mutation -> text expected in a PK-01 title (None: no PK-01, the run must just complete)
MUTATIONS = {
    "tml garbage": (lambda p: _first(p, "DIM_*.tml").write_bytes(bytes(range(256)) * 2), "invalid YAML"),
    "tml empty": (lambda p: _first(p, "DIM_*.tml").write_text(""), "not a TML document"),
    "tml BOM and CRLF": (lambda p: [f.write_bytes(b"\xef\xbb\xbf" + f.read_bytes().replace(b"\n", b"\r\n"))
                                    for f in p.rglob("*.tml")], None),
    "model wrong shape": (lambda p: _first(p, "model.yaml").write_text("model:\n  - {}\n"), "unexpected file shape"),
    "model list items": (lambda p: _first(p, "model.yaml").write_text("model:\n  - tables: [a, b]\n"), "model.yaml"),
    "mstr not a zip": (lambda p: _first(p, "*.mstr").write_bytes(b"PK\x03\x04garbage"), "dashboard could not be read"),
    "mstr damaged delta": (lambda p: _flip_delta(_first(p, "*.mstr")), "truncated or damaged"),
    "macOS litter": (lambda p: [(p / "__MACOSX").mkdir(), (p / "__MACOSX" / "._x.tml").write_bytes(b"\x00\x05\x16\x07"),
                                (_first(p, "*.mstr").parent / "._A.mstr").write_bytes(b"\x00\x05\x16\x07"),
                                (p / "thoughtspot" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")], None),
    "manifest not a mapping": (lambda p: (p / "manifest.yaml").write_text("- a\n- b\n"), "manifest.yaml"),
    "mapping malformed": (lambda p: (p / "mapping.yaml").write_text("fields:\n  - x\nvisualizations: 3\n"), "mapping.yaml"),
    "strategy folder missing": (lambda p: shutil.rmtree(p / "strategy"), "strategy"),
}


@needs_synthetic
@pytest.mark.parametrize("name", MUTATIONS)
def test_damaged_package(name, tmp_path):
    pkg = tmp_path / SID
    shutil.copytree(PACKAGES / SID, pkg)
    mutate, expect = MUTATIONS[name]
    mutate(pkg)
    report = run_rules_only(str(pkg))
    unread = [f.title for f in report.findings if f.check == "PK-01"]
    if expect is None:
        assert not unread, unread
    else:
        assert any(expect in t for t in unread), unread
        assert report.verdict == "NOT_READY"


@needs_synthetic
def test_unreadable_dashboard_is_unverified_not_missing(tmp_path):
    pkg = tmp_path / SID
    shutil.copytree(PACKAGES / SID, pkg)
    _first(pkg, "*.mstr").write_bytes(b"not a zip")
    report = run_rules_only(str(pkg))
    db01 = [f for f in report.findings if f.check == "DB-01" and "no Strategy dashboard" in f.title]
    assert db01 and all(f.status == "UNVERIFIED" for f in db01)
    assert any(f.check == "CV-01" or f.check == "LN-01" for f in report.findings)  # the models were still compared


@needs_synthetic
def test_zipped_package_with_top_folder_and_litter(tmp_path):
    archive = tmp_path / "pkg.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for f in (PACKAGES / SID).rglob("*"):
            if f.is_file():
                z.write(f, f"{SID}/{f.relative_to(PACKAGES / SID)}")
        z.writestr("__MACOSX/._S01", b"\x00\x05\x16\x07")
    report = run_rules_only(str(archive))
    assert not [f for f in report.findings if f.check == "PK-01"]
    assert report.findings


@pytest.fixture
def dead_agent(monkeypatch):
    """A2A client for an agent that does not answer (no waiting between card retries)."""
    import inspector_contract.a2a as a2a
    from inspector_contract.a2a import A2AClient

    monkeypatch.setattr(a2a, "CARD_RETRY_WAITS", ())
    return A2AClient("http://127.0.0.1:9/", timeout=2)


@needs_synthetic
def test_expert_down_gives_a_report(dead_agent):
    from inspector_agent.tools.expert_client import client

    live = client._registered["strategy"]
    client.register("strategy", dead_agent)
    try:
        report = run_rules_only(pkg(SID))
    finally:
        client.register("strategy", live)
    assert report.verdict == "NOT_READY"
    assert any(f.check == "PK-01" and "strategy expert: unreachable" in f.title for f in report.findings)


@needs_synthetic
def test_data_agent_down_is_said_not_hidden(dead_agent):
    from inspector_agent.tools.data_probe import client

    live = client._registered[:]
    client.register(dead_agent)
    try:
        report = run_rules_only(pkg("S04"), with_data=True)
    finally:
        client.register(live[0] if live else None)
    assert report.verdict != "READY"
    assert any("data agent unreachable" in f.title for f in report.findings)
