"""What an expert agent returns for `export_model`: one side of a migration package, in IR."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .ir import Dashboard, ParseIssue, SemanticModel

IR_VERSION = "2"  # 2: supplied SQL removed (the project delivers only TML, .mstr and the model export)


class ModelExport(BaseModel):
    model_config = ConfigDict(frozen=True)

    ir_version: str = IR_VERSION
    tool: str  # thoughtspot | strategy | ...
    location: str  # what was read
    fingerprint: str  # changes when the source files change
    source_format: str | None = None  # e.g. tml, yaml_export, rest_json, ossie_yaml
    models: tuple[SemanticModel, ...] = ()
    dashboards: tuple[Dashboard, ...] = ()
    issues: tuple[ParseIssue, ...] = ()
    status: Literal["ok", "error"] = "ok"
