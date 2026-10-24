from smolagents import ToolCallingAgent, tool
from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallFunction, Model

from commute_agent import agent as agent_module
from commute_agent.agent import (
    MAX_STEPS,
    SYSTEM_PROMPT,
    UNVERIFIED,
    Conversation,
    ModelConfig,
    build_agent,
    missing_model_keys,
    model_config,
    run_turn,
)


class ScriptedModel(Model):
    """Returns the scripted assistant messages in order. No network."""

    def __init__(self, responses: list[ChatMessage]):
        super().__init__()
        self.responses = list(responses)

    def generate(
        self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs
    ):
        return self.responses.pop(0)


def call(name: str, arguments: dict, call_id: str) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            ChatMessageToolCall(
                id=call_id,
                type="function",
                function=ChatMessageToolCallFunction(name=name, arguments=arguments),
            )
        ],
    )


@tool
def plan_trip(origin: str) -> str:
    """Plan a trip from an origin.

    Args:
        origin: The origin place name.
    """
    return "Fastest route takes 42 minutes."


def make_conversation(responses: list[ChatMessage]) -> Conversation:
    agent = ToolCallingAgent(
        tools=[plan_trip],
        model=ScriptedModel(responses),
        instructions=SYSTEM_PROMPT,
        max_steps=MAX_STEPS,
    )
    return Conversation(id="c1", agent=agent)


def test_scripted_tool_call_is_logged_and_final_answer_is_returned() -> None:
    conv = make_conversation(
        [
            call("plan_trip", {"origin": "Fields Corner"}, "t1"),
            call("final_answer", {"answer": "The fastest route takes 42 minutes."}, "f1"),
        ]
    )
    reply = run_turn(conv, "How do I get from Fields Corner?")
    assert reply.text == "The fastest route takes 42 minutes."
    assert [c.name for c in reply.tool_calls] == ["plan_trip"]
    assert reply.tool_calls[0].input == {"origin": "Fields Corner"}
    assert reply.tool_calls[0].is_error is False
    assert "42 minutes" in str(reply.tool_calls[0].output)
    assert reply.unsupported_numbers == []


def test_unsupported_number_triggers_one_rewrite_and_never_reaches_the_user() -> None:
    conv = make_conversation(
        [
            call("plan_trip", {"origin": "Fields Corner"}, "t1"),
            call("final_answer", {"answer": "About 25 minutes, or 42 minutes."}, "f1"),
            call("final_answer", {"answer": "About 42 minutes."}, "f2"),
        ]
    )
    reply = run_turn(conv, "Trip time?")
    assert reply.first_draft_unsupported == ["25"]
    assert reply.unsupported_numbers == []
    assert reply.text == "About 42 minutes."


def test_number_still_unsupported_after_rewrite_is_redacted() -> None:
    conv = make_conversation(
        [
            call("plan_trip", {"origin": "Fields Corner"}, "t1"),
            call("final_answer", {"answer": "Crash risk 2850, time 42 minutes."}, "f1"),
            call("final_answer", {"answer": "Crash risk 2850, time 42 minutes."}, "f2"),
        ]
    )
    reply = run_turn(conv, "Risk?")
    assert reply.unsupported_numbers == ["2850"]
    assert reply.text == f"Crash risk {UNVERIFIED}, time 42 minutes."


@tool
def geocode_place(query: str) -> str:
    """Resolve a place name.

    Args:
        query: The place name.
    """
    return "ok"


@tool
def list_showcase_trips() -> str:
    """List demo trips."""
    return "none"


def test_build_agent_exposes_only_agent_tools(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "OpenAIModel", lambda **kwargs: ScriptedModel([]))
    built = build_agent(
        [plan_trip, geocode_place, list_showcase_trips],
        ModelConfig(model_id="m", api_base="http://localhost:11434/v1", api_key_env=None),
    )
    assert set(built.tools) - {"final_answer"} == {"plan_trip", "geocode_place"}


def test_build_agent_passes_endpoint_and_key(monkeypatch) -> None:
    seen = {}

    def fake_model(**kwargs):
        seen.update(kwargs)
        return ScriptedModel([])

    monkeypatch.setattr(agent_module, "OpenAIModel", fake_model)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    build_agent([plan_trip], model_config_with_defaults(monkeypatch))
    assert seen == {
        "model_id": "deepseek/deepseek-v4.1-flash-1017",
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": "test-key",
    }


def model_config_with_defaults(monkeypatch) -> ModelConfig:
    for name in ("COMMUTE_AGENT_MODEL", "COMMUTE_AGENT_API_BASE", "COMMUTE_AGENT_API_KEY_ENV"):
        monkeypatch.delenv(name, raising=False)
    return model_config()


def test_default_config_is_openrouter_deepseek(monkeypatch) -> None:
    assert model_config_with_defaults(monkeypatch) == ModelConfig(
        model_id="deepseek/deepseek-v4.1-flash-1017",
        api_base="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    )


def test_missing_key_is_named_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert missing_model_keys(model_config_with_defaults(monkeypatch)) == ["OPENROUTER_API_KEY"]


def test_keyless_local_endpoint_needs_no_key(monkeypatch) -> None:
    monkeypatch.setenv("COMMUTE_AGENT_MODEL", "qwen3:8b")
    monkeypatch.setenv("COMMUTE_AGENT_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setenv("COMMUTE_AGENT_API_KEY_ENV", "")
    config = model_config()
    assert config == ModelConfig(
        model_id="qwen3:8b", api_base="http://localhost:11434/v1", api_key_env=None
    )
    assert missing_model_keys(config) == []


def test_load_env_reads_the_file_without_overriding_the_shell(tmp_path, monkeypatch) -> None:
    from commute_agent.agent import load_env

    env = tmp_path / ".env"
    env.write_text("COMMUTE_TEST_FROM_FILE=file\nCOMMUTE_TEST_SHELL_WINS=file\n")
    monkeypatch.delenv("COMMUTE_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("COMMUTE_TEST_SHELL_WINS", "shell")
    load_env(env)
    import os

    assert os.environ["COMMUTE_TEST_FROM_FILE"] == "file"
    assert os.environ["COMMUTE_TEST_SHELL_WINS"] == "shell"
    monkeypatch.delenv("COMMUTE_TEST_FROM_FILE")
