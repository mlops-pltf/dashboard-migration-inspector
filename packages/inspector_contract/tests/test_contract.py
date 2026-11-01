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


def test_a2a_run_keeps_the_callers_context_inside_an_event_loop():
    """A host's trace context (contextvars) must reach the request even when the call runs on a helper thread."""
    import asyncio
    import contextvars

    from inspector_contract.a2a import _run

    var = contextvars.ContextVar("trace", default=None)

    async def read():
        return var.get()

    async def caller():
        var.set("trace-123")
        return _run(read())  # inside a running loop: _run switches to a thread

    assert asyncio.run(caller()) == "trace-123"


def test_agentcore_runtime_arn_becomes_its_invocation_url():
    from bedrock_agentcore.runtime.a2a import build_runtime_url

    from inspector_contract.a2a import A2AClient, runtime_url

    arn = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/strategy_agent-AbC123"
    assert runtime_url(arn).rstrip("/") == build_runtime_url(arn)  # same as the AgentCore SDK
    assert A2AClient(arn).url == build_runtime_url(arn) + "/"  # the card is fetched from <url>/.well-known/...
    assert runtime_url("http://localhost:9002/") == "http://localhost:9002/"


def test_plugins_named_in_the_environment_are_imported(tmp_path, monkeypatch):
    (tmp_path / "host_plugin.py").write_text("LOADED = True\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("INSPECTOR_PLUGINS", "host_plugin, ")
    from inspector_contract import plugins

    assert plugins.load() == ["host_plugin"]
    import host_plugin
    assert host_plugin.LOADED
