"""Turn what a developer uploads into a migration package folder the agents can read.

Accepted per side:
  ThoughtSpot: a TML export zip (as ThoughtSpot downloads it), or loose .tml files
  Strategy:    an export zip (model + dashboards/ + sql/), or a single model file (YAML export, REST JSON, Ossie YAML)
  Mapping:     optional mapping.yaml
Or one whole-package zip with thoughtspot/ and strategy/ inside.

Zips are untrusted: no absolute paths, no "..", no links, size and file-count limits.
"""

from __future__ import annotations

import io
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 5000
TS_SUFFIXES = {".tml", ".sql", ".yaml", ".yml", ".json"}
ST_SUFFIXES = {".yaml", ".yml", ".json"}


class UploadError(ValueError):
    pass


@dataclass
class Upload:
    filename: str
    content: bytes


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:40] or "package"


def _members(data: bytes) -> list[tuple[PurePosixPath, bytes]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadError(f"not a valid zip: {exc}") from exc
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise UploadError(f"zip has {len(infos)} files (limit {MAX_FILES})")
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        raise UploadError("zip is larger than 200 MB unpacked")
    out = []
    for info in infos:
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", info.filename):
            raise UploadError(f"unsafe path in zip: {info.filename}")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise UploadError(f"links are not allowed in zips: {info.filename}")
        if path.parts[0] in ("__MACOSX",) or path.name.startswith("."):
            continue
        out.append((path, zf.read(info)))
    return out


def _strip_common_root(members: list[tuple[PurePosixPath, bytes]]) -> list[tuple[PurePosixPath, bytes]]:
    """A zip of one folder (Sales Orders/...) is treated as that folder's contents."""
    while members and all(len(p.parts) > 1 for p, _ in members) and len({p.parts[0] for p, _ in members}) == 1:
        members = [(PurePosixPath(*p.parts[1:]), b) for p, b in members]
    return members


def _write(target: Path, rel: PurePosixPath, data: bytes) -> None:
    dest = (target / rel).resolve()
    if not dest.is_relative_to(target.resolve()):
        raise UploadError(f"unsafe path: {rel}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _strategy_place(name: str, data: bytes) -> PurePosixPath:
    """Where a loose Strategy JSON belongs: dashboards/, sql/ or the model itself."""
    if name.lower().endswith(".json"):
        try:
            import json

            doc = json.loads(data)
        except ValueError:
            return PurePosixPath(name)
        if isinstance(doc, dict) and "chapters" in doc:
            return PurePosixPath("dashboards", name)
        if isinstance(doc, dict) and "sqlStatement" in doc:
            return PurePosixPath("sql", name)
    return PurePosixPath(name)


def _side(target: Path, uploads: list[Upload], suffixes: set[str], strategy: bool) -> int:
    count = 0
    for up in uploads:
        name = PurePosixPath(up.filename).name
        if name.lower().endswith(".zip"):
            for rel, data in _strip_common_root(_members(up.content)):
                if rel.suffix.lower() in suffixes:
                    _write(target, rel, data)
                    count += 1
        elif PurePosixPath(name).suffix.lower() in suffixes:
            _write(target, _strategy_place(name, up.content) if strategy else PurePosixPath(name), up.content)
            count += 1
        else:
            raise UploadError(f"{name}: expected a zip or {', '.join(sorted(suffixes))}")
    return count


def build_package(root: Path, package_id: str, name: str, thoughtspot: list[Upload], strategy: list[Upload],
                  mapping: Upload | None = None, whole: Upload | None = None) -> Path:
    """Write the package folder <root>/<package_id>; returns it. Raises UploadError with a readable reason."""
    folder = root / package_id
    if folder.exists():
        raise UploadError(f"package {package_id} exists already")
    folder.mkdir(parents=True)
    try:
        if whole is not None:
            members = _strip_common_root(_members(whole.content))
            if not any(p.parts[0] == "thoughtspot" for p, _ in members) or \
                    not any(p.parts[0] == "strategy" for p, _ in members):
                raise UploadError("a whole-package zip needs a thoughtspot/ and a strategy/ folder")
            for rel, data in members:
                if rel.parts[0] in ("thoughtspot", "strategy") or rel.name in ("mapping.yaml", "manifest.yaml"):
                    _write(folder, rel, data)
        else:
            if not thoughtspot or not strategy:
                raise UploadError("upload both sides: the ThoughtSpot export and the Strategy export")
            if not _side(folder / "thoughtspot", thoughtspot, TS_SUFFIXES, strategy=False):
                raise UploadError("no TML files found in the ThoughtSpot upload")
            if not _side(folder / "strategy", strategy, ST_SUFFIXES, strategy=True):
                raise UploadError("no Strategy files found in the Strategy upload")
            if mapping is not None:
                try:
                    yaml.safe_load(mapping.content)
                except yaml.YAMLError as exc:
                    raise UploadError(f"mapping file is not valid YAML: {exc}") from exc
                _write(folder, PurePosixPath("mapping.yaml"), mapping.content)
        manifest = folder / "manifest.yaml"
        doc = (yaml.safe_load(manifest.read_text()) or {}) if manifest.exists() else {}
        doc.update({"package_id": package_id, "description": doc.get("description") or name})
        manifest.write_text(yaml.safe_dump(doc, sort_keys=False))
        if not list((folder / "thoughtspot").rglob("*.tml")):
            raise UploadError("no TML files found on the ThoughtSpot side")
        return folder
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
