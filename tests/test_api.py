import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from commute_agent import mcp_server
from commute_agent.api import IDENTITY_NOTICE, create_app
from commute_agent.paths import EMISSION_FACTORS, PROCESSED


class InProcessTool:
    """Stands in for an MCPClient tool: calls the FastMCP object in this process."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, **arguments: Any) -> Any:
        try:
            _, structured = asyncio.run(mcp_server.mcp.call_tool(self.name, arguments))
        except Exception as error:  # the SDK reports tool errors; mirror MCPClient's string form
            return f"Error executing tool {self.name}: {error}"
        return structured


def in_process_tools() -> dict[str, InProcessTool]:
    return {
        tool.name: InProcessTool(tool.name) for tool in mcp_server.mcp._tool_manager.list_tools()
    }


@pytest.fixture
def client(server, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("COMMUTE_AGENT_MODEL", raising=False)
    monkeypatch.delenv("COMMUTE_AGENT_API_BASE", raising=False)
    monkeypatch.delenv("COMMUTE_AGENT_API_KEY_ENV", raising=False)
    with TestClient(create_app(live=False, tool_source=in_process_tools)) as test_client:
        yield test_client


PLAN_BODY = {
    "origin": {"lat": 42.3550, "lon": -71.0650},
    "destination": {"lat": 42.3580, "lon": -71.0620},
    "depart_at": "2026-10-27T08:15:00-04:00",
}


def test_root_serves_the_page(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "<html" in response.text.lower()


def test_status_reports_offline_and_the_notice(client: TestClient) -> None:
    body = client.get("/api/status").json()
    assert body["run_mode"] == "offline"
    assert body["notice"] == IDENTITY_NOTICE
    assert body["agent_available"] is False
    assert body["missing_model_keys"] == ["OPENROUTER_API_KEY"]


def test_chat_without_credentials_is_503_naming_model_and_key(client: TestClient) -> None:
    response = client.post("/api/chat", json={"conversation_id": None, "message": "hi"})
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The agent model deepseek/deepseek-v4.1-flash-1017 needs OPENROUTER_API_KEY."
    )


def test_plan_returns_full_plan(client: TestClient) -> None:
    response = client.post("/api/plan", json=PLAN_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["routes"]
    assert any(leg["coords"] for route in body["routes"] for leg in route["legs"])


def test_plan_outside_boston_is_422(client: TestClient) -> None:
    body = {**PLAN_BODY, "origin": {"lat": 42.5, "lon": -71.0}}
    response = client.post("/api/plan", json=body)
    assert response.status_code == 422
    assert "origin" in response.json()["detail"]


def test_preferences_round_trip(client: TestClient) -> None:
    assert client.get("/api/preferences").json()["insufficient_data"] is True
    assert client.delete("/api/preferences").json()["n_choices"] == 0


def test_alerts_maps_ids_to_headers_for_the_active_source(client: TestClient) -> None:
    assert client.get("/api/alerts").json()["alerts"] == []
    synthetic = "Synthetic: Red Line suspended JFK/UMass to Park Street"
    assert client.post("/api/alert-source", json={"name": synthetic}).status_code == 200
    body = client.get("/api/alerts").json()
    assert body["source"] == synthetic
    assert body["synthetic"] is True
    headers = {alert["id"]: alert["header"] for alert in body["alerts"]}
    assert headers["synthetic-red-1"].startswith("Synthetic demo alert: Red Line suspended")


def test_alert_source_is_per_session(client: TestClient) -> None:
    synthetic = "Synthetic: Red Line suspended JFK/UMass to Park Street"
    tab_a = {"X-Session-Id": "tab-a"}
    tab_b = {"X-Session-Id": "tab-b"}
    set_a = client.post("/api/alert-source", json={"name": synthetic}, headers=tab_a)
    assert set_a.json()["active"] == synthetic
    assert client.get("/api/alert-sources", headers=tab_b).json()["active"] == "none"
    assert client.get("/api/alerts", headers=tab_b).json()["alerts"] == []
    assert client.post("/api/plan", json=PLAN_BODY, headers=tab_a).json()["alert_source"] == (
        synthetic
    )
    assert client.post("/api/plan", json=PLAN_BODY, headers=tab_b).json()["alert_source"] == "none"


def test_unknown_alert_source_is_422_and_not_stored(client: TestClient) -> None:
    assert client.post("/api/alert-source", json={"name": "nope"}).status_code == 422
    assert client.get("/api/alert-sources").json()["active"] == "none"


def test_alert_source_lists_none_and_live(client: TestClient) -> None:
    body = client.get("/api/alert-sources").json()
    assert body["active"] == "none"
    assert "none" in body["sources"]
    assert body["live_enabled"] is False


@pytest.mark.skipif(
    not (PROCESSED / "walk.graphml").exists() or not EMISSION_FACTORS.exists(),
    reason=(
        "needs data/processed built by the M2 ingest and risk build, and the "
        f"user-supplied {EMISSION_FACTORS} (the MCP server subprocess exits 2 without it)"
    ),
)
def test_real_stdio_subprocess_serves_status() -> None:
    with TestClient(create_app(live=False)) as real_client:
        assert real_client.get("/api/status").json()["run_mode"] == "offline"


def test_geocode_resolves_a_station_and_reports_unknown_places(client: TestClient) -> None:
    found = client.get("/api/geocode", params={"q": "Park Street"}).json()
    assert found["status"] == "ok"
    assert found["candidates"][0]["name"] == "Park Street"
    assert client.get("/api/geocode", params={"q": "Zzyzx"}).json()["status"] == "not_found"


def test_healthz_is_ok(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"ok": True}


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def limited(server, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeClock]:
    """A client with no model key, so an accepted chat request answers 503 without a model call."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("COMMUTE_AGENT_MODEL", raising=False)
    monkeypatch.delenv("COMMUTE_AGENT_API_BASE", raising=False)
    monkeypatch.delenv("COMMUTE_AGENT_API_KEY_ENV", raising=False)
    monkeypatch.delenv("COMMUTE_ALLOWED_HOSTS", raising=False)
    clock = FakeClock()
    app = create_app(live=False, tool_source=in_process_tools, clock=clock)
    with TestClient(app) as test_client:
        yield test_client, clock


CHAT = {"conversation_id": None, "message": "hi"}


def test_chat_per_session_limit_is_429_until_the_window_passes(
    limited: tuple[TestClient, FakeClock],
) -> None:
    client, clock = limited
    tab_a = {"X-Session-Id": "tab-a"}
    for _ in range(5):
        assert client.post("/api/chat", json=CHAT, headers=tab_a).status_code == 503
    blocked = client.post("/api/chat", json=CHAT, headers=tab_a)
    assert blocked.status_code == 429
    assert (
        blocked.json()["detail"] == "Too many chat messages from this session. Try again in 60 s."
    )
    assert blocked.headers["Retry-After"] == "60"
    tab_b = {"X-Session-Id": "tab-b"}
    assert client.post("/api/chat", json=CHAT, headers=tab_b).status_code == 503
    clock.now += 30
    assert client.post("/api/chat", json=CHAT, headers=tab_a).status_code == 429
    clock.now += 30
    assert client.post("/api/chat", json=CHAT, headers=tab_a).status_code == 503


def test_chat_global_limit_is_429_across_sessions(limited: tuple[TestClient, FakeClock]) -> None:
    client, _ = limited
    for i in range(60):
        response = client.post("/api/chat", json=CHAT, headers={"X-Session-Id": f"tab-{i}"})
        assert response.status_code == 503
    blocked = client.post("/api/chat", json=CHAT, headers={"X-Session-Id": "tab-new"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Too many chat messages from the service. Try again in 60 s."


def test_disallowed_host_is_400_and_allowed_host_passes(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMUTE_ALLOWED_HOSTS", "localhost, Commute-Agent.example")
    with TestClient(create_app(live=False, tool_source=in_process_tools)) as client:
        assert client.get("/healthz", headers={"Host": "evil.example:8000"}).status_code == 400
        assert client.get("/healthz", headers={"Host": "localhost:8000"}).status_code == 200
        assert client.get("/healthz", headers={"Host": "commute-agent.example"}).status_code == 200


def test_unset_allowed_hosts_allows_any_host(server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMMUTE_ALLOWED_HOSTS", raising=False)
    with TestClient(create_app(live=False, tool_source=in_process_tools)) as client:
        assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 200
