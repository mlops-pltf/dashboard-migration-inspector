"""The report's fixed vocabulary: verdict names and one neutral label per severity and status. Everything else
people read (titles, explanations, fixes, the brief) is written by the agent from the findings."""

from __future__ import annotations

VERDICT = {"READY": "Ready", "NOT_READY": "Not ready", "NEEDS_REVIEW": "Needs review"}


def label(severity: str, status: str) -> str:
    if status == "PASS":
        return "Confirmed"
    if status == "UNVERIFIED":
        return "Could not confirm" + (": may change numbers" if severity == "BLOCKER" else "")
    return {"BLOCKER": "Must fix: changes numbers", "WARNING": "Worth checking", "LOW": "Cosmetic",
            "INFO": "For information"}[severity]
