"""Write migration packages (Inspector's input) and ground truth (kept outside the packages)."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import yaml

from . import sqlgen, strategy, thoughtspot
from .domain import TABLE_INFO
from .scenarios import Query, Scenario


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def dump_yaml(obj) -> str:
    return yaml.dump(obj, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120, default_flow_style=False)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _json(obj) -> str:
    return json.dumps(obj, indent=2) + "\n"


def _query_sql(sem, q: Query) -> str:
    return sqlgen.build(sem, q.dims, q.measures, q.filters, q.sort, q.top_n) + "\n"


def write_package(sc: Scenario, root: Path) -> Path:
    pkg = root / sc.id
    if pkg.exists():
        shutil.rmtree(pkg)
    ts_dir, st_dir = pkg / "thoughtspot", pkg / "strategy"
    model = sc.ts_model

    # ThoughtSpot: Table TML for every table the model uses, the worksheet/model, liveboards and answers
    for table in model.tables:
        _write(ts_dir / f"{table}.table.tml", dump_yaml(thoughtspot.table_tml(table)))
    if model.style == "worksheet":
        _write(ts_dir / f"{model.name}.worksheet.tml", dump_yaml(thoughtspot.worksheet_tml(model)))
    else:
        _write(ts_dir / f"{model.name}.model.tml", dump_yaml(thoughtspot.model_tml(model)))
    ts_sem = sc.ts_semantics()
    for lb in sc.ts_content:
        if lb.kind == "answer":
            _write(ts_dir / f"{lb.name}.answer.tml", dump_yaml(thoughtspot.answer_tml(model, lb)))
        else:
            _write(ts_dir / f"{lb.name}.liveboard.tml", dump_yaml(thoughtspot.liveboard_tml(model, lb)))
        for v in lb.visualizations:
            sql = sqlgen.build(ts_sem, v.dims, v.measures, lb.filters, v.sort, v.top_n)
            _write(ts_dir / "sql" / lb.name / f"{v.name}.sql",
                   f"-- ThoughtSpot SQL for {lb.kind} '{lb.name}', visualization '{v.name}'\n{sql}\n")

    # Strategy: model in the scenario's format, dashboards, SQL view
    spec = sc.strategy
    model_doc, builder = strategy.build_model(spec)
    if sc.strategy_format == "yaml_export":
        _write(st_dir / "model.yaml", strategy.yaml_export(model_doc, dump_yaml))
    elif sc.strategy_format == "rest_json":
        for name, doc in strategy.rest_json(model_doc).items():
            _write(st_dir / "model" / name, _json(doc))
    else:
        _write(st_dir / "model.ossie.yaml", dump_yaml(strategy.ossie_yaml(spec)))
    for lb in spec.content:
        _write(st_dir / "dashboards" / f"{lb.name}.json", _json(strategy.dashboard(spec, builder, lb)))
    _write(st_dir / "sql" / "model_sqlview.json", _json(strategy.model_sql_view(spec)))

    manifest = {
        "package_id": sc.id,
        "description": f"Migration of ThoughtSpot {model.style} '{model.name}' and its content to Strategy "
                       f"model '{spec.name}'.",
        "thoughtspot": {"tml_dir": "thoughtspot/", "sql_dir": "thoughtspot/sql/"},
        "strategy": {
            "model": {"yaml_export": "strategy/model.yaml", "rest_json": "strategy/model/",
                      "ossie_yaml": "strategy/model.ossie.yaml"}[sc.strategy_format],
            "model_format": sc.strategy_format,
            "dashboards_dir": "strategy/dashboards/",
            "sql_dir": "strategy/sql/",
        },
    }
    if sc.mapping:
        _write(pkg / "mapping.yaml", dump_yaml(sc.mapping))
        manifest["mapping"] = "mapping.yaml"
    _write(pkg / "manifest.yaml", dump_yaml(manifest))
    return pkg


def write_ground_truth(sc: Scenario, root: Path) -> Path:
    gt = root / sc.id
    if gt.exists():
        shutil.rmtree(gt)
    ts_sem, st_sem = sc.ts_semantics(), sc.strategy_semantics()
    witnesses = []
    for w in sc.witnesses:
        ts_file, st_file = f"witnesses/{w.id}.thoughtspot.sql", f"witnesses/{w.id}.strategy.sql"
        _write(gt / ts_file, _query_sql(ts_sem, w.thoughtspot))
        _write(gt / st_file, _query_sql(st_sem, w.strategy or w.thoughtspot))
        witnesses.append({"id": w.id, "description": w.description, "expect": w.expect,
                          "thoughtspot_sql": ts_file, "strategy_sql": st_file})

    def finding(e) -> dict:
        out = {k: v for k, v in asdict(e).items() if v not in (None, (), False, [])}
        return {k: list(v) if isinstance(v, tuple) else v for k, v in out.items()}

    doc = {
        "scenario": sc.id,
        "title": sc.title,
        "description": sc.description,
        "thoughtspot_model": f"{sc.ts_model.name} ({sc.ts_model.style})",
        "strategy_model": sc.strategy.name,
        "strategy_style": sc.strategy.style,
        "strategy_format": sc.strategy_format,
        "expected_verdict": sc.expected_verdict,
    }
    if sc.expected_verdict_after_data:
        doc["expected_verdict_after_data"] = sc.expected_verdict_after_data
    doc["findings"] = [finding(e) for e in sc.expected]
    doc["must_pass"] = list(sc.must_pass)
    doc["witnesses"] = witnesses
    _write(gt / "expected_findings.yaml", dump_yaml(doc))
    return gt
