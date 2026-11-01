"""Where package files live. A location is a string; its scheme picks the reader:

    /path/to/folder, /path/to/package.zip, file://...   local (a shared mount locally)
    s3://bucket/prefix/                                 S3 (AWS credentials from the environment or the runtime role;
                                                        AWS_ENDPOINT_URL for an S3-compatible store)
    <scheme>://...                                      anything a host adds with register_reader()

``materialize(location)`` returns a local folder plus a fingerprint that changes when the files change.
Local paths must sit under PACKAGE_ROOT when that is set (several folders separated by ':', e.g. the synthetic
packages and the portal's uploads; relative paths resolve against the first).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

Reader = Callable[[str], tuple[Path, str]]  # location -> (local folder, fingerprint)
_READERS: dict[str, Reader] = {}


def is_junk(path: Path, root: Path) -> bool:
    """Operating-system litter, not package content: macOS resource forks (._x, __MACOSX), .DS_Store, hidden files."""
    return any(part.startswith(".") or part == "__MACOSX" for part in path.relative_to(root).parts)


def package_files(root: Path, pattern: str) -> list[Path]:
    """Files under root matching pattern (recursive), without operating-system litter."""
    return sorted(p for p in root.rglob(pattern) if p.is_file() and not is_junk(p, root))


def _publish(tmp: Path, target: Path) -> None:
    """Move a fully written folder into place, so an interrupted extract or download is never reused."""
    try:
        tmp.rename(target)
    except OSError:  # another worker got there first
        shutil.rmtree(tmp, ignore_errors=True)


def _roots() -> list[Path]:
    return [Path(r).resolve() for r in os.environ.get("PACKAGE_ROOT", "").split(os.pathsep) if r.strip()]


def _local(location: str) -> Path:
    roots = _roots()
    path = Path(location.removeprefix("file://"))
    if roots:
        path = (roots[0] / path) if not path.is_absolute() else path
        path = path.resolve()
        uploads = Path(tempfile.gettempdir()).resolve()
        if not (any(path.is_relative_to(r) for r in roots) or path.is_relative_to(uploads)):
            raise ValueError(f"location must be inside {' or '.join(str(r) for r in roots)}")
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"not found: {location}")
    return path


def _fingerprint_dir(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file() and not is_junk(p, path):
            st = p.stat()
            h.update(f"{p.relative_to(path)}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:16]


def _unzip(path: Path, digest: str) -> Path:
    """Extracted once per zip content (a re-uploaded zip with the same name gets a fresh folder). A zip that
    wraps everything in one top folder is entered."""
    target = Path(tempfile.gettempdir()) / f"pkg-{digest}"
    if not target.exists():
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                if Path(member).is_absolute() or ".." in Path(member).parts:
                    raise ValueError(f"unsafe path in zip: {member}")
            tmp = Path(tempfile.mkdtemp(prefix="pkg-partial-"))
            zf.extractall(tmp)
        _publish(tmp, target)
    inner = [p for p in target.iterdir() if not is_junk(p, target)]
    return inner[0] if len(inner) == 1 and inner[0].is_dir() else target


def _s3(location: str) -> tuple[Path, str]:
    import boto3

    bucket, _, prefix = location.removeprefix("s3://").partition("/")
    inside = ""
    if (m := re.search(r"\.zip/", prefix, re.IGNORECASE)) is not None:  # a folder inside a zipped package
        prefix, inside = prefix[:m.end() - 1], prefix[m.end():].strip("/")
    s3 = boto3.client("s3")
    objects = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        objects.extend(page.get("Contents", []))
    if not objects:
        raise FileNotFoundError(f"nothing under {location}")
    fp = hashlib.sha256("".join(f"{o['Key']}:{o['ETag']}" for o in objects).encode()).hexdigest()[:16]
    if prefix.lower().endswith(".zip"):  # one zipped package: download it, then unpack like a local zip
        archive = Path(tempfile.gettempdir()) / f"s3-{fp}.zip"
        if not archive.exists():
            tmp = archive.with_suffix(".partial")
            s3.download_file(bucket, prefix, str(tmp))
            tmp.rename(archive)
        root = _unzip(archive, fp)
        if inside and not (root / inside).is_dir():
            raise FileNotFoundError(f"not found: {location}")
        return (root / inside if inside else root), fp
    target = Path(tempfile.gettempdir()) / f"s3-{fp}"
    if not target.exists():
        tmp = Path(tempfile.mkdtemp(prefix="s3-partial-"))
        for o in objects:
            rel = o["Key"][len(prefix):].lstrip("/")
            if not rel or rel.endswith("/") or ".." in Path(rel).parts:
                continue
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, o["Key"], str(dest))
        _publish(tmp, target)
    return target, fp


def register_reader(scheme: str, reader: Reader) -> None:
    """Add a source of packages, e.g. a host's artifact store: locations `<scheme>://...` go to `reader`, which
    returns a local folder holding the package and a fingerprint that changes when the files change."""
    _READERS[scheme.lower()] = reader


def scheme_of(location: str) -> str:
    return location.split("://", 1)[0].lower() if "://" in location else ""


def materialize(location: str) -> tuple[Path, str]:
    scheme = scheme_of(location)
    if scheme in _READERS:
        return _READERS[scheme](location)
    if scheme not in ("", "file"):
        raise ValueError(f"no reader for {scheme}:// locations (register_reader)")
    path = _local(location)
    if path.suffix.lower() == ".zip":
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        return _unzip(path, digest), digest
    return path, _fingerprint_dir(path)


def join(location: str, *parts: str) -> str:
    """Child location (works for local paths and S3 prefixes)."""
    return "/".join([location.rstrip("/"), *parts])


register_reader("s3", _s3)
