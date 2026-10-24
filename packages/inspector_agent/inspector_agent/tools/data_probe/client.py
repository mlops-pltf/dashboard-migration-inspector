"""A2A client for the data agent.

  INSPECTOR_DATA_AGENT_URL   default http://localhost:9003/
  INSPECTOR_A2A_AUTH         none | sigv4
Tests (or another runtime) can register their own client.
"""

from __future__ import annotations

import os

from inspector_contract.a2a import A2AClient

_registered: list[A2AClient] = []


def register(client: A2AClient | None) -> None:
    _registered.clear()
    if client is not None:
        _registered.append(client)


def data_agent() -> A2AClient:
    if not _registered:
        _registered.append(A2AClient(os.environ.get("INSPECTOR_DATA_AGENT_URL", "http://localhost:9003/"),
                                     auth=os.environ.get("INSPECTOR_A2A_AUTH", "none"),
                                     region=os.environ.get("AWS_REGION")))
    return _registered[0]


def call(skill: str, args: dict) -> dict:
    try:
        return data_agent().call(skill, args)
    except Exception as exc:  # data agent down: the probe is not available, never a pass
        return {"error": f"data agent unreachable: {type(exc).__name__}: {exc}"}
