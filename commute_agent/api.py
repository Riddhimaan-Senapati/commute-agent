"""FastAPI server: REST endpoints, the chat agent, and the static web page on 127.0.0.1."""

import argparse
import logging
import math
import os
import sys
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import anyio
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp import StdioServerParameters
from mcp.client.stdio import get_default_environment
from pydantic import BaseModel
from smolagents import MCPClient

from commute_agent.agent import (
    TOOL_ERROR_PREFIX,
    Conversation,
    build_agent,
    load_env,
    missing_model_keys,
    model_config,
    run_turn,
)
from commute_agent.emissions import EmissionFactorsError, load_emission_factors
from commute_agent.models import AgentReply, Point
from commute_agent.paths import PROCESSED, WEB

IDENTITY_NOTICE = "Independent prototype. Not a City of Boston or MBTA service."
NO_AUTH_NOTICE = "No authentication. Local demo use only."
HOST = "127.0.0.1"
# The MCP subprocess loads ~330 MB of street graphs before it answers; slow hosts need minutes.
MCP_CONNECT_TIMEOUT_S = 300
# The MCP stdio client starts the server with a minimal environment; these pass through.
MCP_ENV_PASSTHROUGH = ("COMMUTE_OTP_URL",)
# Each browser tab sends an X-Session-Id and gets its own alert source and route choices.
# Requests without one share the "" session. The oldest sessions are forgotten past MAX_SESSIONS.
DEFAULT_ALERT_SOURCE = "none"
MAX_SESSIONS = 10_000
# Chat is the only endpoint that spends model credits. Each limit is (requests, window in seconds).
CHAT_LIMIT_PER_SESSION = (5, 60)
CHAT_LIMIT_GLOBAL = (60, 60)
# Comma-separated Host names the API accepts, for example "localhost,commute-agent.example".
# Unset or empty allows every Host. Behind the Next.js proxy, list the host the API sees.
ALLOWED_HOSTS_ENV = "COMMUTE_ALLOWED_HOSTS"

SessionId = Annotated[str, Header(alias="X-Session-Id", max_length=64)]

logger = logging.getLogger("commute_agent.api")


class PlanBody(BaseModel):
    origin: Point
    destination: Point
    depart_at: str | None = None


class AlertSourceBody(BaseModel):
    name: str


class ChoiceBody(BaseModel):
    query_id: str
    route_id: str


class ChatBody(BaseModel):
    conversation_id: str | None = None
    message: str


ToolSource = Callable[[], dict[str, Any]]


class SlidingWindow:
    """Recent request times per key. A request is allowed when fewer than ``limit`` of the
    key's requests fall in the last ``window`` seconds. ``clock`` is injectable for tests."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.hits: dict[str, deque[float]] = {}

    def wait_seconds(self, key: str, limit: int, window: float) -> float:
        """0.0 when a request under ``key`` is allowed now, else seconds until one is."""
        now = self.clock()
        times = self.hits.get(key)
        if times is None:
            return 0.0
        while times and now - times[0] >= window:
            times.popleft()
        if not times:
            del self.hits[key]
            return 0.0
        if len(times) < limit:
            return 0.0
        # The (limit)th most recent hit must age out before one more request fits.
        return times[-limit] + window - now

    def record(self, key: str) -> None:
        """Note one allowed request under ``key``. Past MAX_SESSIONS keys, the oldest is dropped."""
        self.hits.setdefault(key, deque()).append(self.clock())
        if len(self.hits) > MAX_SESSIONS:
            del self.hits[next(iter(self.hits))]


def allowed_hosts() -> frozenset[str]:
    """COMMUTE_ALLOWED_HOSTS as lowercase names. An empty set means every Host is allowed."""
    names = os.environ.get(ALLOWED_HOSTS_ENV, "").split(",")
    return frozenset(name.strip().lower() for name in names if name.strip())


def host_name(header: str) -> str:
    """The host part of a Host header without its port. IPv6 literals keep their brackets."""
    if header.startswith("["):
        return header[: header.find("]") + 1].lower()
    return header.split(":")[0].lower()


def chat_limited(scope: str, wait: float) -> HTTPException:
    seconds = math.ceil(wait)
    return HTTPException(
        status_code=429,
        detail=f"Too many chat messages from {scope}. Try again in {seconds} s.",
        headers={"Retry-After": str(seconds)},
    )


def call_tool(app: FastAPI, tool: str, /, **arguments: Any) -> dict:
    """Call one MCP tool. A tool error string becomes HTTP 422 with the server's message.

    ``tool`` is positional-only so a tool argument called ``name`` (set_alert_source) can
    pass through ``arguments``."""
    result = app.state.tools[tool](**arguments)
    if isinstance(result, str) and result.startswith(TOOL_ERROR_PREFIX):
        raise HTTPException(status_code=422, detail=result)
    return result


def create_app(
    live: bool,
    tool_source: ToolSource | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """Build the app. ``tool_source`` replaces the MCP subprocess in tests, and ``clock``
    drives the chat rate limits."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = model_config()
        app.state.live = live
        app.state.model_config = config
        app.state.missing_keys = missing_model_keys(config)
        app.state.conversations = {}
        app.state.alert_sources = {}
        with ExitStack() as stack:
            if tool_source is not None:
                app.state.tools = tool_source()
            else:
                args = ["-m", "commute_agent.mcp_server", *(["--live"] if live else [])]
                env = get_default_environment() | {
                    name: os.environ[name] for name in MCP_ENV_PASSTHROUGH if name in os.environ
                }
                params = StdioServerParameters(command=sys.executable, args=args, env=env)
                tools = stack.enter_context(
                    MCPClient(
                        params,
                        structured_output=True,
                        adapter_kwargs={"connect_timeout": MCP_CONNECT_TIMEOUT_S},
                    )
                )
                app.state.tools = {tool.name: tool for tool in tools}
            yield

    app = FastAPI(title="Pareto Commute", lifespan=lifespan)
    session_limits = SlidingWindow(clock)
    global_limits = SlidingWindow(clock)
    hosts = allowed_hosts()

    if hosts:

        @app.middleware("http")
        async def host_guard(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            if host_name(request.headers.get("host", "")) not in hosts:
                return JSONResponse({"detail": "Host not allowed."}, status_code=400)
            return await call_next(request)

    def alert_source(session: str) -> str:
        return app.state.alert_sources.get(session, DEFAULT_ALERT_SOURCE)

    def remember_alert_source(session: str, name: str) -> None:
        sources: dict[str, str] = app.state.alert_sources
        sources.pop(session, None)
        sources[session] = name
        if len(sources) > MAX_SESSIONS:
            del sources[next(iter(sources))]

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/api/status")
    def status() -> dict:
        config = app.state.model_config
        missing = app.state.missing_keys
        return {
            "run_mode": "live" if app.state.live else "offline",
            "agent_model": config.model_id,
            "agent_available": not missing,
            "missing_model_keys": missing,
            "notice": IDENTITY_NOTICE,
        }

    @app.get("/api/showcase-trips")
    def showcase_trips() -> dict:
        return call_tool(app, "list_showcase_trips")

    @app.post("/api/plan")
    def plan(body: PlanBody, session: SessionId = "") -> dict:
        created = call_tool(
            app,
            "plan_trip",
            origin_lat=body.origin.lat,
            origin_lon=body.origin.lon,
            destination_lat=body.destination.lat,
            destination_lon=body.destination.lon,
            depart_at=body.depart_at,
            alert_source=alert_source(session),
            session_id=session,
        )
        return call_tool(app, "get_plan", query_id=created["query_id"])

    @app.get("/api/alert-sources")
    def alert_sources(session: SessionId = "") -> dict:
        return {**call_tool(app, "list_alert_sources"), "active": alert_source(session)}

    @app.post("/api/alert-source")
    def set_alert_source(body: AlertSourceBody, session: SessionId = "") -> dict:
        # get_active_alerts rejects an unknown source, or live in offline mode, with a 422.
        call_tool(app, "get_active_alerts", alert_source=body.name)
        remember_alert_source(session, body.name)
        return alert_sources(session)

    @app.get("/api/alerts")
    def alerts(session: SessionId = "") -> dict:
        return call_tool(app, "get_active_alerts", alert_source=alert_source(session))

    @app.get("/api/geocode")
    def geocode(q: str) -> dict:
        return call_tool(app, "geocode_place", query=q)

    @app.post("/api/choice")
    def choice(body: ChoiceBody, session: SessionId = "") -> dict:
        return call_tool(
            app,
            "record_choice",
            query_id=body.query_id,
            route_id=body.route_id,
            session_id=session,
        )

    @app.get("/api/preferences")
    def preferences(session: SessionId = "") -> dict:
        return call_tool(app, "get_preferences", session_id=session)

    @app.delete("/api/preferences")
    def clear_preferences(session: SessionId = "") -> dict:
        return call_tool(app, "clear_preferences", session_id=session)

    @app.post("/api/chat", response_model=AgentReply)
    async def chat(body: ChatBody, session: SessionId = "") -> AgentReply:
        session_wait = session_limits.wait_seconds(session, *CHAT_LIMIT_PER_SESSION)
        if session_wait:
            raise chat_limited("this session", session_wait)
        global_wait = global_limits.wait_seconds("", *CHAT_LIMIT_GLOBAL)
        if global_wait:
            raise chat_limited("the service", global_wait)
        session_limits.record(session)
        global_limits.record("")
        config = app.state.model_config
        missing = app.state.missing_keys
        if missing:
            detail = f"The agent model {config.model_id} needs {', '.join(missing)}."
            raise HTTPException(status_code=503, detail=detail)
        conversations: dict[str, Conversation] = app.state.conversations
        conv = conversations.get(body.conversation_id or "")
        if conv is None:
            agent = build_agent(
                list(app.state.tools.values()),
                config,
                lambda: alert_source(session),
                lambda: session,
            )
            conv = Conversation(id=uuid.uuid4().hex[:8], agent=agent)
            conversations[conv.id] = conv
        return await anyio.to_thread.run_sync(run_turn, conv, body.message)

    @app.get("/basemap.geojson")
    def basemap() -> FileResponse:
        path: Path = PROCESSED / "basemap.geojson"
        if not path.exists():
            raise HTTPException(status_code=404, detail="basemap.geojson has not been built")
        return FileResponse(path, media_type="application/geo+json")

    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pareto Commute API and web page.")
    parser.add_argument("--live", action="store_true", help="Enable live MBTA and OTP calls.")
    parser.add_argument(
        "--model",
        help="Model id on the configured endpoint, for example deepseek/deepseek-v4.1-flash-1017.",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--host",
        default=HOST,
        help="Bind address. Default 127.0.0.1; use 0.0.0.0 only behind a hosted proxy.",
    )
    args = parser.parse_args(argv)
    load_env()
    if args.model:
        os.environ["COMMUTE_AGENT_MODEL"] = args.model
    try:
        load_emission_factors()
    except EmissionFactorsError as error:
        print(f"emission factors: {error}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO)
    logger.warning(NO_AUTH_NOTICE)
    # One process: the MCP subprocess holds plans and choices, and this process holds
    # each session's alert source and conversations. Hosts that set
    # WEB_CONCURRENCY must not fork extra workers.
    uvicorn.run(create_app(live=args.live), host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
