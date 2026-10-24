"""Inspector tests run against the real expert agents and data agent, in-process, over the real A2A protocol."""

from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3] / "synthetic"
PACKAGES = ROOT / "packages"
GROUND_TRUTH = ROOT / "ground_truth"
IDS = sorted(p.name for p in PACKAGES.iterdir()) if PACKAGES.exists() else []

needs_synthetic = pytest.mark.skipif(not IDS, reason="run `uv run inspector-synth generate` first")


def pkg(sid: str) -> str:
    return str(PACKAGES / sid)


@pytest.fixture(scope="session", autouse=True)
def expert_team():
    from inspector_contract.a2a import A2AClient
    from inspector_agent.tools.expert_client.client import register
    from strategy_agent.strategy_agent import app as strategy_app
    from thoughtspot_agent.thoughtspot_agent import app as thoughtspot_app

    register("thoughtspot", A2AClient("http://thoughtspot-agent/",
                                      transport=httpx.ASGITransport(app=thoughtspot_app(conversation=False))))
    register("strategy", A2AClient("http://strategy-agent/",
                                   transport=httpx.ASGITransport(app=strategy_app(conversation=False))))
    from data_agent.data_agent import app as data_app
    from inspector_agent.tools.data_probe.client import register as register_data

    register_data(A2AClient("http://data-agent/", transport=httpx.ASGITransport(app=data_app(conversation=False))))
    yield


def warehouse_up() -> bool:
    try:
        from data_agent.tools.warehouse.connection import connect

        connect().close()
        return True
    except Exception:
        return False


needs_warehouse = pytest.mark.skipif(not warehouse_up(), reason="warehouse not reachable (docker compose up warehouse)")
