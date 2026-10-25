"""YAML/JSON loading that keeps line numbers, so every IR object can point back to its source."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from inspector_contract.ir import SourceRef

# A ruamel YAML instance is not thread-safe: concurrent requests sharing one corrupt each other's parse
# ("while parsing a block mapping" on valid files). One instance per thread.
_local = threading.local()


def _parser() -> YAML:
    if not hasattr(_local, "yaml"):
        _local.yaml = YAML(typ="rt")
    return _local.yaml


def load_yaml(path: Path) -> Any:
    with open(path) as fh:
        return _parser().load(fh)


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text())


def line_of(node: Any, key: str | int | None = None) -> int | None:
    """1-based line of ``node[key]`` (or of ``node`` itself) when the node came from YAML."""
    try:
        if key is None:
            return node.lc.line + 1
        if isinstance(node, CommentedMap):
            return node.lc.key(key)[0] + 1
        if isinstance(node, CommentedSeq):
            return node.lc.item(key)[0] + 1
    except (AttributeError, KeyError, IndexError, TypeError):
        return None
    return None


class Locator:
    """Builds SourceRefs for one file."""

    def __init__(self, file: Path, root: Path | None = None):
        self.file = str(file.relative_to(root)) if root and file.is_relative_to(root) else str(file)

    def ref(self, path: str, node: Any = None, key: str | int | None = None, object_id: str | None = None) -> SourceRef:
        return SourceRef(file=self.file, path=path, line=line_of(node, key) if node is not None else None,
                         object_id=object_id)


def plain(value: Any) -> Any:
    """ruamel containers -> plain dict/list/str (for storing in IR settings)."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, str):
        return str(value)
    return value
