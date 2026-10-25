"""thoughtspot_agent: the ThoughtSpot expert, an A2A server (port 9000) on Bedrock AgentCore.

Structured skills (answered by code, never by the LLM):
  export_model {location}            -> ModelExport (IR) of a ThoughtSpot folder
  analyze_sql  {sql, default_schema} -> SQL lineage
  list_fields  {location, model?}    -> columns with definitions
  get_object   {location, name}      -> one object in detail
Conversation: any text message, answered by the Strands agent with its tools.
"""

from __future__ import annotations

import os
from pathlib import Path

from strands import Agent
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from inspector_contract.a2a import SkillRouterExecutor, agent_card

from .llm import get_model, warm_up
from .tools.model_export.export import export
from .tools.model_export.tool import summarize_export
from .tools.sql_analyzer.tool import analyze_sql, analyze_sql_impl
from .tools.thoughtspot_reader.tool import get_object_impl, get_thoughtspot_object, list_fields_impl, list_thoughtspot_fields

NAME = "thoughtspot_agent"
DESCRIPTION = "ThoughtSpot expert: reads TML, formulas and search queries and exports them as IR."
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text()
TOOLS = [summarize_export, list_thoughtspot_fields, get_thoughtspot_object, analyze_sql]

SKILLS = {
    "export_model": lambda a: export(a["location"]).model_dump(mode="json"),
    "analyze_sql": lambda a: analyze_sql_impl(a["sql"], a.get("default_schema", "public")),
    "list_fields": lambda a: list_fields_impl(a["location"], a.get("model")),
    "get_object": lambda a: get_object_impl(a["location"], a["name"]),
}
SKILL_DOCS = {
    "export_model": "ThoughtSpot folder -> ModelExport (IR): models, joins, fields, filters, liveboards, SQL.",
    "analyze_sql": "Lineage, joins and filters of a SELECT.",
    "list_fields": "Columns of a worksheet/model with definitions and source lines.",
    "get_object": "One worksheet/model, table, column or liveboard in detail.",
}


def build_agent(model=None) -> Agent:
    return Agent(model=model or get_model("strong"), tools=TOOLS, system_prompt=SYSTEM_PROMPT,
                 callback_handler=None, name=NAME)


def executor(conversation: bool = True) -> SkillRouterExecutor:
    chat = StrandsA2AExecutor(agent_factory=lambda _context_id: build_agent()) if conversation else None
    return SkillRouterExecutor(SKILLS, chat)


def card(url: str):
    return agent_card(NAME, DESCRIPTION, url, SKILL_DOCS,
                      conversation="Ask about a ThoughtSpot export: formulas, joins, filters, liveboards, semantics.")


def app(url: str = "http://localhost:9000/", conversation: bool = True):
    """The A2A ASGI app (used by tests and by `main`)."""
    from bedrock_agentcore.runtime import build_a2a_app

    return build_a2a_app(executor(conversation), card(url), runtime_url=url)


def main() -> None:
    from bedrock_agentcore.runtime import serve_a2a

    from inspector_contract import telemetry

    telemetry.setup(NAME)
    warm_up()
    serve_a2a(executor(), card(os.environ.get("AGENTCORE_RUNTIME_URL", "http://localhost:9000/")))


if __name__ == "__main__":
    main()
