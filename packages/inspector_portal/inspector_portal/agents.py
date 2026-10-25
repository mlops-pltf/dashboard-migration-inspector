"""The portal's view of the agent team: the Inspector for runs and questions, the experts for upload checks.

  PORTAL_INSPECTOR_URL     default http://localhost:9000/
  PORTAL_THOUGHTSPOT_URL   default http://localhost:9001/
  PORTAL_STRATEGY_URL      default http://localhost:9002/
  PORTAL_A2A_AUTH          none | sigv4
"""

from __future__ import annotations

import os

from inspector_contract.a2a import A2AClient

_DEFAULTS = {"inspector": "http://localhost:9000/", "thoughtspot": "http://localhost:9001/",
             "strategy": "http://localhost:9002/"}


def client(name: str, timeout: float = 3600) -> A2AClient:
    url = os.environ.get(f"PORTAL_{name.upper()}_URL", _DEFAULTS[name])
    return A2AClient(url, auth=os.environ.get("PORTAL_A2A_AUTH", "none"), region=os.environ.get("AWS_REGION"),
                     timeout=timeout)


def check_package(location: str) -> dict:
    """Ask each expert to read its side: what it found and what it could not read. Nothing is validated."""
    out: dict = {"ok": True, "sides": {}}
    for side in ("thoughtspot", "strategy"):
        try:
            exp = client(side, timeout=300).call("export_model", {"location": f"{location}/{side}"})
        except Exception as exc:
            out["ok"] = False
            out["sides"][side] = {"error": f"{side} expert unreachable: {type(exc).__name__}"}
            continue
        if "error" in exp:
            out["ok"] = False
            out["sides"][side] = {"error": exp["error"]}
            continue
        errors = [i["message"] for i in exp.get("issues", []) if i.get("severity") == "error"]
        out["ok"] = out["ok"] and not errors and bool(exp.get("models"))
        out["sides"][side] = {
            "format": exp.get("source_format"),
            "models": [m["name"] for m in exp.get("models", [])],
            "dashboards": [d["name"] for d in exp.get("dashboards", [])],
            "errors": errors,
            "warnings": [i["message"] for i in exp.get("issues", []) if i.get("severity") != "error"][:20],
        }
    return out
