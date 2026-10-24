"""FastAPI server: REST endpoints, the chat agent, and the static web page on 127.0.0.1."""

import argparse
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mcp import StdioServerParameters
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


def call_tool(app: FastAPI, tool: str, /, **arguments: Any) -> dict:
    """Call one MCP tool. A tool error string becomes HTTP 422 with the server's message.

    ``tool`` is positional-only so a tool argument called ``name`` (set_alert_source) can
    pass through ``arguments``."""
    result = app.state.tools[tool](**arguments)
    if isinstance(result, str) and result.startswith(TOOL_ERROR_PREFIX):
        raise HTTPException(status_code=422, detail=result)
    return result


def create_app(live: bool, tool_source: ToolSource | None = None) -> FastAPI:
    """Build the app. ``tool_source`` replaces the MCP subprocess in tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = model_config()
        app.state.live = live
        app.state.model_config = config
        app.state.missing_keys = missing_model_keys(config)
        app.state.conversations = {}
        with ExitStack() as stack:
            if tool_source is not None:
                app.state.tools = tool_source()
            else:
                args = ["-m", "commute_agent.mcp_server", *(["--live"] if live else [])]
                params = StdioServerParameters(command=sys.executable, args=args)
                tools = stack.enter_context(MCPClient(params, structured_output=True))
                app.state.tools = {tool.name: tool for tool in tools}
            yield

    app = FastAPI(title="Pareto Commute", lifespan=lifespan)

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
    def plan(body: PlanBody) -> dict:
        created = call_tool(
            app,
            "plan_trip",
            origin_lat=body.origin.lat,
            origin_lon=body.origin.lon,
            destination_lat=body.destination.lat,
            destination_lon=body.destination.lon,
            depart_at=body.depart_at,
        )
        return call_tool(app, "get_plan", query_id=created["query_id"])

    @app.get("/api/alert-sources")
    def alert_sources() -> dict:
        return call_tool(app, "list_alert_sources")

    @app.post("/api/alert-source")
    def set_alert_source(body: AlertSourceBody) -> dict:
        return call_tool(app, "set_alert_source", name=body.name)

    @app.get("/api/alerts")
    def alerts() -> dict:
        return call_tool(app, "get_active_alerts")

    @app.post("/api/choice")
    def choice(body: ChoiceBody) -> dict:
        return call_tool(app, "record_choice", query_id=body.query_id, route_id=body.route_id)

    @app.get("/api/preferences")
    def preferences() -> dict:
        return call_tool(app, "get_preferences")

    @app.delete("/api/preferences")
    def clear_preferences() -> dict:
        return call_tool(app, "clear_preferences")

    @app.post("/api/chat", response_model=AgentReply)
    async def chat(body: ChatBody) -> AgentReply:
        config = app.state.model_config
        missing = app.state.missing_keys
        if missing:
            detail = f"The agent model {config.model_id} needs {', '.join(missing)}."
            raise HTTPException(status_code=503, detail=detail)
        conversations: dict[str, Conversation] = app.state.conversations
        conv = conversations.get(body.conversation_id or "")
        if conv is None:
            agent = build_agent(list(app.state.tools.values()), config)
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
    uvicorn.run(create_app(live=args.live), host=HOST, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
