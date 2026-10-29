"""The contract: IR round-trips as JSON; the skill router answers structured requests in code."""

import httpx
import pytest
from bedrock_agentcore.runtime import build_a2a_app

from inspector_contract import IR_VERSION, Agg, Join, ModelExport, PCol, PhysicalColumn, SemanticModel
from inspector_contract.a2a import A2AClient, SkillRouterExecutor, agent_card


def test_model_export_round_trips():
    expr = Agg(fn="SUM", arg=PCol(column=PhysicalColumn(namespace="retail", table="t", column="x")))
    from inspector_contract import Field
    model = SemanticModel(id="m", name="M", tool="strategy", kind="mosaic",
                          joins=(Join(id="j", left="a", right="b", type="UNKNOWN", keys=(("k", "k"),), applied="always"),),
                          fields=(Field(id="f", name="F", kind="MEASURE", expr=expr, object_type="x", provenance="interpreted"),))
    exp = ModelExport(tool="strategy", location="/p", fingerprint="abc", models=(model,))
    again = ModelExport.model_validate_json(exp.model_dump_json())
    assert again == exp and again.ir_version == IR_VERSION


@pytest.fixture
def client():
    skills = {"double": lambda a: {"value": a["x"] * 2}, "boom": lambda a: 1 / 0}
    card = agent_card("t", "test agent", "http://t/", {"double": "doubles x", "boom": "fails"})
    app = build_a2a_app(SkillRouterExecutor(skills), card, runtime_url="http://t/")
    return A2AClient("http://t/", transport=httpx.ASGITransport(app=app))


def test_structured_skills(client):
    assert client.call("double", {"x": 21}) == {"value": 42}
    assert "ZeroDivisionError" in client.call("boom", {})["error"]
    assert client.call("nope", {})["skills"] == ["boom", "double"]


def test_text_without_conversation_lists_skills(client):
    text, _ = client.ask("hello")
    assert "double" in text


def test_card(client):
    card = client.card()
    assert [s.id for s in card.skills] == ["double", "boom"]
