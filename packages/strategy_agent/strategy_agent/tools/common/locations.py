"""Where package files live: a local folder (shared mount), a local zip, or an S3 prefix (AgentCore).

``materialize(location)`` returns a local folder plus a fingerprint that changes when the files change.
Local paths must sit under PACKAGE_ROOT when that is set.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from pathlib import Path


def _root() -> Path | None:
    root = os.environ.get("PACKAGE_ROOT")
    return Path(root).resolve() if root else None


def _local(location: str) -> Path:
    root = _root()
    path = Path(location.removeprefix("file://"))
    if root is not None:
        path = (root / path) if not path.is_absolute() else path
        path = path.resolve()
        uploads = Path(tempfile.gettempdir()).resolve()
        if not (path.is_relative_to(root) or path.is_relative_to(uploads)):
            raise ValueError(f"location must be inside {root}")
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"not found: {location}")
    return path


def _fingerprint_dir(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(f"{p.relative_to(path)}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:16]


def _unzip(path: Path) -> Path:
    target = Path(tempfile.gettempdir()) / f"pkg-{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    if not target.exists():
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                if Path(member).is_absolute() or ".." in Path(member).parts:
                    raise ValueError(f"unsafe path in zip: {member}")
            zf.extractall(target)
    inner = [p for p in target.iterdir() if p.is_dir()]
    return inner[0] if len(inner) == 1 and not any(target.glob("*.*")) else target


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
        for o in objects:
            rel = o["Key"][len(prefix):].lstrip("/")
            if not rel or ".." in Path(rel).parts:
                continue
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, o["Key"], str(dest))
    return target, fp


def materialize(location: str) -> tuple[Path, str]:
    if location.startswith("s3://"):
        return _s3(location)
    path = _local(location)
    if path.suffix.lower() == ".zip":
        return _unzip(path), hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return path, _fingerprint_dir(path)


def join(location: str, *parts: str) -> str:
    """Child location (works for local paths and S3 prefixes)."""
    return "/".join([location.rstrip("/"), *parts])
