"""A2A clients for the expert agents, one per side.

Configured by environment (local compose or AgentCore runtime URLs):
  INSPECTOR_THOUGHTSPOT_AGENT_URL   default http://localhost:9001/
  INSPECTOR_STRATEGY_AGENT_URL      default http://localhost:9002/
  INSPECTOR_A2A_AUTH                none | sigv4   (sigv4 on AgentCore)
  AWS_REGION                        for sigv4
Tests (or another runtime) can register their own clients.
"""

from __future__ import annotations

import os

from inspector_contract.a2a import A2AClient

SIDES = ("thoughtspot", "strategy")
_DEFAULTS = {"thoughtspot": "http://localhost:9001/", "strategy": "http://localhost:9002/"}
_registered: dict[str, A2AClient] = {}


def register(side: str, client: A2AClient) -> None:
    _registered[side] = client


def expert(side: str) -> A2AClient:
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    if side not in _registered:
        url = os.environ.get(f"INSPECTOR_{side.upper()}_AGENT_URL", _DEFAULTS[side])
        _registered[side] = A2AClient(url, auth=os.environ.get("INSPECTOR_A2A_AUTH", "none"),
                                      region=os.environ.get("AWS_REGION"))
    return _registered[side]
