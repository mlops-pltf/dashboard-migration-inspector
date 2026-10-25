"""What changed since the previous run of the same package: fixed, still open, new."""

from __future__ import annotations

OPEN = {"FAIL", "UNVERIFIED"}


def _key(f: dict) -> tuple:
    side = lambda s: (f.get(s) or {}).get("name")  # noqa: E731
    return (f.get("check"), side("thoughtspot"), side("strategy"))


def _label(f: dict) -> dict:
    return {"check": f.get("check"), "severity": f.get("severity"), "status": f.get("status"), "title": f.get("title")}


def compare(current: dict, previous: dict) -> dict:
    """Both are Inspector reports (report JSON). Findings are matched by check and the objects on each side."""
    now = {_key(f): f for f in current.get("findings", []) if f.get("status") in OPEN}
    before = {_key(f): f for f in previous.get("findings", []) if f.get("status") in OPEN}
    return {
        "previous_verdict": previous.get("verdict"),
        "verdict": current.get("verdict"),
        "fixed": [_label(f) for k, f in before.items() if k not in now],
        "still_open": [_label(f) for k, f in now.items() if k in before],
        "new": [_label(f) for k, f in now.items() if k not in before],
    }
