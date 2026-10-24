import json
from pathlib import Path

import psycopg
import pytest
import yaml

from inspector_synth import package, verify, warehouse
from inspector_synth.scenarios import SCENARIOS

CHECK_IDS = {
    "CV-01", "CV-02", "CV-03", "LN-01", "LN-02", "DT-01",
    "JN-01", "JN-02", "JN-03", "JN-04", "JN-05", "JN-06", "JN-07",
    "AG-01", "AG-02", "AG-03", "AG-04", "FL-01", "FL-02", "FL-03",
    "FX-01", "FX-02", "FX-03", "FX-04", "FX-05",
    "DB-01", "DB-02", "DB-03", "DB-04", "DB-05", "DB-06", "LF-01", "LF-02",
}


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("synth")
    for sc in SCENARIOS.values():
        package.write_package(sc, root / "packages")
        package.write_ground_truth(sc, root / "ground_truth")
    return root


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_eighteen_scenarios():
    assert sorted(SCENARIOS) == [f"S{i:02d}" for i in range(1, 19)]


def test_generation_is_deterministic(generated, tmp_path):
    for sc in SCENARIOS.values():
        package.write_package(sc, tmp_path / "packages")
        package.write_ground_truth(sc, tmp_path / "ground_truth")
    assert _files(tmp_path) == _files(generated)


def test_every_artifact_parses(generated):
    for path in (generated / "packages").rglob("*"):
        if path.suffix in (".tml", ".yaml"):
            assert yaml.safe_load(path.read_text()), path
        elif path.suffix == ".json":
            assert json.loads(path.read_text()), path


def test_packages_do_not_leak_ground_truth(generated):
    titles = [sc.title for sc in SCENARIOS.values()]
    for path in (generated / "packages").rglob("*"):
        if path.is_file():
            text = path.read_text()
            assert "expected_findings" not in text and "witness" not in text.lower(), path
            assert not any(t in text for t in titles), path


def test_each_package_has_both_sides(generated):
    for sid, sc in SCENARIOS.items():
        pkg = generated / "packages" / sid
        assert list((pkg / "thoughtspot").glob("*.table.tml"))
        assert list((pkg / "thoughtspot").glob(f"*.{sc.ts_model.style}.tml"))
        model = yaml.safe_load((pkg / "manifest.yaml").read_text())["strategy"]["model"]
        assert (pkg / model).exists()
        assert list((pkg / "strategy" / "dashboards").glob("*.json"))


def test_yaml_export_fingerprint_matches_body(generated):
    from inspector_synth.ids import fingerprint

    text = (generated / "packages" / "S01" / "strategy" / "model.yaml").read_text()
    body, _, last = text.rstrip("\n").rpartition("\n")
    assert last == f'fingerprint: "{fingerprint(body + chr(10))}"'


def test_ground_truth_uses_catalogue_check_ids():
    for sc in SCENARIOS.values():
        for e in sc.expected:
            assert e.check in CHECK_IDS, (sc.id, e.check)
            assert set(e.also_acceptable) <= CHECK_IDS, (sc.id, e.also_acceptable)


def test_ready_scenarios_have_no_blocker_failures():
    for sc in SCENARIOS.values():
        blockers = [e for e in sc.expected if e.severity == "BLOCKER" and e.status == "FAIL"]
        assert (sc.expected_verdict == "NOT_READY") == bool(blockers), sc.id


def test_warehouse_contains_the_traps():
    rows = warehouse.generate()
    customers = {r["customer_id"] for r in rows["dim_customer"]}
    products = {r["product_id"] for r in rows["dim_product"]}
    orders = rows["fact_orders"]
    assert 0.02 < sum(o["customer_id"] not in customers for o in orders) / len(orders) < 0.04
    assert any(line["product_id"] not in products for line in rows["fact_order_lines"])
    assert any(o["discount_amount"] is None for o in orders)
    assert any(o["order_amount"] == 0 and o["order_status"] == "Replacement" for o in orders)
    assert any(o["order_ts"].hour == 0 and o["order_date"] < o["order_ts"].date() for o in orders)
    dates = {r["date_key"] for r in rows["dim_date"]}
    assert all(o["order_date"] in dates for o in orders)  # S06 relies on dim_date being complete


def _warehouse_up() -> bool:
    try:
        psycopg.connect(verify.DEFAULT_DSN, connect_timeout=2).close()
        return True
    except psycopg.OperationalError:
        return False


@pytest.mark.skipif(not _warehouse_up(), reason="warehouse not running (docker compose up -d warehouse)")
def test_witnesses_behave_as_expected(generated):
    failed = [o for o in verify.run(generated / "ground_truth") if not o.ok]
    assert not failed, failed
