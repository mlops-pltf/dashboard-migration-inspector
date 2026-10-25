"""A2A conventions for the Inspector team.

Every agent is an A2A server. Two kinds of request:
  * structured skill: a message with a DataPart {"skill": "<name>", "args": {...}}. Answered by code, never by an
    LLM, as a DataPart with the JSON result (e.g. export_model returns a ModelExport).
  * conversation: any other message (text). Answered by the agent's Strands agent (StrandsA2AExecutor).

Authentication between agents: none locally, SigV4 on AgentCore (``auth="sigv4"``).
Tracing: the client sends the W3C trace context with every request and the server continues it (telemetry.py).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    AgentCapabilities, AgentCard, AgentSkill, DataPart, Message, Part, Role, Task, TextPart,
)
from a2a.utils import new_agent_parts_message, new_agent_text_message

from . import telemetry

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"

Handler = Callable[[dict], dict]


# ---------------------------------------------------------------------------------------------
# Server side
# ---------------------------------------------------------------------------------------------
def structured_request(message: Message | None) -> tuple[str, dict] | None:
    for part in (message.parts if message else []):
        root = part.root
        if isinstance(root, DataPart) and isinstance(root.data, dict) and "skill" in root.data:
            return str(root.data["skill"]), dict(root.data.get("args") or {})
    return None


class SkillRouterExecutor(AgentExecutor):
    """Structured skills go to code; everything else to the conversational executor (a Strands agent)."""

    def __init__(self, skills: dict[str, Handler], conversation: AgentExecutor | None = None):
        self.skills = skills
        self.conversation = conversation

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        headers = (context.call_context.state.get("headers") if context.call_context else None) or {}
        with telemetry.continued_from(headers):
            await self._execute(context, event_queue)

    async def _execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        agent = os.environ.get("OTEL_SERVICE_NAME", "agent")
        request = structured_request(context.message)
        if request is not None:
            name, args = request
            handler = self.skills.get(name)
            with telemetry.span(f"{agent}: {name}", input=args, **{"a2a.skill": name}) as s:
                if handler is None:
                    result: dict = {"error": f"unknown skill {name!r}", "skills": sorted(self.skills)}
                else:
                    try:
                        result = await asyncio.to_thread(handler, args)  # copies the trace context
                    except Exception as exc:  # reported to the caller, not raised into the server
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                s.output(result)
            await event_queue.enqueue_event(new_agent_parts_message(
                [Part(root=DataPart(data=result))], context.context_id, context.task_id))
            return
        if self.conversation is None:
            await event_queue.enqueue_event(new_agent_text_message(
                f"Send a structured request; skills: {sorted(self.skills)}", context.context_id, context.task_id))
            return
        with telemetry.span(f"{agent}: ask", input=context.get_user_input(), **{"a2a.skill": "ask"}):
            await self.conversation.execute(context, event_queue)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if self.conversation is not None:
            await self.conversation.cancel(context, event_queue)


def agent_card(name: str, description: str, url: str, structured: dict[str, str],
               conversation: str | None = None, version: str = "0.1.0") -> AgentCard:
    skills = [AgentSkill(id=k, name=k, description=v, tags=["structured"], input_modes=["application/json"],
                         output_modes=["application/json"]) for k, v in structured.items()]
    if conversation:
        skills.append(AgentSkill(id="ask", name="ask", description=conversation, tags=["conversation"],
                                 input_modes=["text/plain"], output_modes=["text/plain"]))
    return AgentCard(name=name, description=description, url=url, version=version,
                     capabilities=AgentCapabilities(streaming=False),
                     default_input_modes=["text/plain", "application/json"],
                     default_output_modes=["text/plain", "application/json"], skills=skills)


# ---------------------------------------------------------------------------------------------
# Client side
# ---------------------------------------------------------------------------------------------
class SigV4(httpx.Auth):
    """Signs requests for AgentCore Runtime (service bedrock-agentcore) with the caller's AWS credentials."""

    requires_request_body = True

    def __init__(self, region: str):
        import boto3

        self.region = region
        self.credentials = boto3.Session().get_credentials()

    def auth_flow(self, request: httpx.Request):
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        headers = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", SESSION_HEADER.lower())}
        aws = AWSRequest(method=request.method, url=str(request.url), data=request.content, headers=headers)
        SigV4Auth(self.credentials.get_frozen_credentials(), "bedrock-agentcore", self.region).add_auth(aws)
        request.headers.update(dict(aws.headers.items()))
        yield request


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:  # already inside an event loop
        return pool.submit(asyncio.run, coro).result()


class A2AClient:
    def __init__(self, url: str, auth: str = "none", region: str | None = None, timeout: float = 900,
                 transport: httpx.AsyncBaseTransport | None = None, session_id: str | None = None):
        self.url = url if url.endswith("/") else url + "/"
        self.auth = SigV4(region or "us-east-1") if auth == "sigv4" else None
        self.timeout = timeout
        self.transport = transport
        self.session_id = session_id or f"inspector-{uuid.uuid4().hex}"  # AgentCore needs >= 33 chars

    @property
    def name(self) -> str:
        return httpx.URL(self.url).host or self.url

    async def _send(self, message: Message, headers: dict[str, str]) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout, auth=self.auth, transport=self.transport,
                                     headers={SESSION_HEADER: self.session_id, **headers}) as http:
            card = await A2ACardResolver(httpx_client=http, base_url=self.url).get_agent_card()
            card = card.model_copy(update={"url": self.url})  # we know where we reached it
            client = ClientFactory(ClientConfig(httpx_client=http, streaming=False)).create(card)
            async for event in client.send_message(message):
                return event[0] if isinstance(event, tuple) else event
        return None

    def card(self) -> AgentCard:
        async def get():
            async with httpx.AsyncClient(timeout=self.timeout, auth=self.auth, transport=self.transport,
                                         headers={SESSION_HEADER: self.session_id}) as http:
                return await A2ACardResolver(httpx_client=http, base_url=self.url).get_agent_card()
        return _run(get())

    def call(self, skill: str, args: dict) -> dict:
        msg = Message(role=Role.user, message_id=uuid.uuid4().hex,
                      parts=[Part(root=DataPart(data={"skill": skill, "args": args}))])
        # the trace context is taken here, in the caller's context (_run may switch threads)
        with telemetry.span(f"call {self.name}: {skill}", input=args) as s:
            reply = _run(self._send(msg, telemetry.inject({})))
            for part in _parts(reply):
                if isinstance(part.root, DataPart):
                    s.output(part.root.data)
                    return part.root.data
            return {"error": "no structured answer", "text": _text(reply)}

    def ask(self, question: str, context_id: str | None = None) -> tuple[str, str | None]:
        msg = Message(role=Role.user, message_id=uuid.uuid4().hex, context_id=context_id,
                      parts=[Part(root=TextPart(text=question))])
        with telemetry.span(f"ask {self.name}", input=question) as s:
            reply = _run(self._send(msg, telemetry.inject({})))
            s.output(_text(reply))
        return _text(reply), getattr(reply, "context_id", None)


def _parts(reply: Any) -> list[Part]:
    if isinstance(reply, Message):
        return list(reply.parts)
    if isinstance(reply, Task):
        parts = [p for a in (reply.artifacts or []) for p in a.parts]
        if reply.status and reply.status.message:
            parts += list(reply.status.message.parts)
        return parts
    return []


def _text(reply: Any) -> str:
    return "\n".join(p.root.text for p in _parts(reply) if isinstance(p.root, TextPart))
