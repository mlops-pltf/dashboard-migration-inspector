"""Where package files live: a local folder (shared mount), a local zip, or an S3 prefix (AgentCore).

``materialize(location)`` returns a local folder plus a fingerprint that changes when the files change.
Local paths must sit under PACKAGE_ROOT when that is set (several folders separated by ':', e.g. the synthetic
packages and the portal's uploads; relative paths resolve against the first).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path


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
    s3 = boto3.client("s3")
    objects = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        objects.extend(page.get("Contents", []))
    if not objects:
        raise FileNotFoundError(f"nothing under {location}")
    fp = hashlib.sha256("".join(f"{o['Key']}:{o['ETag']}" for o in objects).encode()).hexdigest()[:16]
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


def materialize(location: str) -> tuple[Path, str]:
    if location.startswith("s3://"):
        return _s3(location)
    path = _local(location)
    if path.suffix.lower() == ".zip":
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        return _unzip(path, digest), digest
    return path, _fingerprint_dir(path)


def join(location: str, *parts: str) -> str:
    """Child location (works for local paths and S3 prefixes)."""
    return "/".join([location.rstrip("/"), *parts])
