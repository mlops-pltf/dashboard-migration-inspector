"""Deterministic identifiers in each tool's native format."""

import hashlib
import uuid

_TS_NAMESPACE = uuid.UUID("5f0c6d1e-2b7a-4c1e-9a53-7d2f4e6b8a10")


def strategy_id(*parts: str) -> str:
    """32-char uppercase hex, the Strategy objectId format."""
    return hashlib.md5("|".join(parts).encode()).hexdigest().upper()


def ts_guid(*parts: str) -> str:
    """UUID, the ThoughtSpot GUID format."""
    return str(uuid.uuid5(_TS_NAMESPACE, "|".join(parts)))


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
