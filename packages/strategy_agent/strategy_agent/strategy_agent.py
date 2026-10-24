"""strategy_agent: the Strategy (Mosaic) expert, an A2A server (port 9000) on Bedrock AgentCore.

Structured skills (answered by code, never by the LLM):
  export_model {location}            -> ModelExport (IR) of a Strategy folder
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

from .llm import get_model
from .tools.model_export.export import export
from .tools.model_export.tool import summarize_export
from .tools.sql_analyzer.tool import analyze_sql, analyze_sql_impl
from .tools.strategy_reader.tool import get_object_impl, get_strategy_object, list_fields_impl, list_strategy_fields

NAME = "strategy_agent"
DESCRIPTION = "Strategy (Mosaic) expert: reads model exports, metrics, Freeform SQL and dashboards and exports them as IR."
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text()
TOOLS = [summarize_export, list_strategy_fields, get_strategy_object, analyze_sql]

SKILLS = {
    "export_model": lambda a: export(a["location"]).model_dump(mode="json"),
    "analyze_sql": lambda a: analyze_sql_impl(a["sql"], a.get("default_schema", "public")),
    "list_fields": lambda a: list_fields_impl(a["location"], a.get("model")),
    "get_object": lambda a: get_object_impl(a["location"], a["name"]),
}
SKILL_DOCS = {
    "export_model": "Strategy folder -> ModelExport (IR): models, derived joins, fields, Freeform SQL lineage, dashboards, SQL.",
    "analyze_sql": "Lineage, joins and filters of a SELECT.",
    "list_fields": "Attributes and metrics with definitions and source lines.",
    "get_object": "The model, a logical table, an attribute/metric or a dashboard in detail.",
}


def build_agent(model=None) -> Agent:
    return Agent(model=model or get_model("strong"), tools=TOOLS, system_prompt=SYSTEM_PROMPT,
                 callback_handler=None, name=NAME)


def executor(conversation: bool = True) -> SkillRouterExecutor:
    chat = StrandsA2AExecutor(agent_factory=lambda _context_id: build_agent()) if conversation else None
    return SkillRouterExecutor(SKILLS, chat)


def card(url: str):
    return agent_card(NAME, DESCRIPTION, url, SKILL_DOCS,
                      conversation="Ask about a Strategy export: metrics, attributes, joins, Freeform SQL, dashboards, semantics.")


def app(url: str = "http://localhost:9000/", conversation: bool = True):
    """The A2A ASGI app (used by tests and by `main`)."""
    from bedrock_agentcore.runtime import build_a2a_app

    return build_a2a_app(executor(conversation), card(url), runtime_url=url)


def main() -> None:
    from bedrock_agentcore.runtime import serve_a2a

    serve_a2a(executor(), card(os.environ.get("AGENTCORE_RUNTIME_URL", "http://localhost:9000/")))


if __name__ == "__main__":
    main()
