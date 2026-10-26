"""Chat agent: a smolagents ToolCallingAgent over MCP tools, on an OpenAI-compatible model."""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from smolagents import OpenAIModel, Tool, ToolCallingAgent
from smolagents.memory import ActionStep

from commute_agent.grounding import numbers_in, unsupported_numbers
from commute_agent.models import AgentReply, ToolCall
from commute_agent.paths import ROOT

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_API_KEY_ENV = "OPENROUTER_API_KEY"
MAX_STEPS = 8
AGENT_TOOLS = (
    "geocode_place",
    "plan_trip",
    "compare_routes",
    "get_active_alerts",
    "record_choice",
    "get_preferences",
)
ALERT_SOURCE_ARG = "alert_source"
SESSION_ID_ARG = "session_id"
CONVERSATION_ARGS = (ALERT_SOURCE_ARG, SESSION_ID_ARG)
TOOL_ERROR_PREFIX = "Error executing tool"
UNVERIFIED = "[number removed: not in tool results]"
GROUNDING_RETRY = (
    "Your last reply contains numbers that appear in no tool result: {numbers}. "
    "Rewrite the whole reply. Copy every number from a tool result, or round it only to fewer "
    "decimal places (2849.014 may become 2849 or 2849.0, not 2850). Do not add new numbers."
)
FINAL_ANSWER = "final_answer"

IDENTITY_SENTENCE = "I am an independent prototype, not a City of Boston or MBTA service."
SYSTEM_PROMPT_TEMPLATE = """\
You are the Pareto Commute prototype, a hackathon project running on a swappable language model. Begin your first reply of every conversation with this exact sentence: "{identity}"

You help people compare trips within the City of Boston. For a trip question:
1. Resolve each place with geocode_place. If the status is ambiguous or not_found, ask the user which place they mean. Do not pick one.
2. Call plan_trip. If it reports a point outside the City of Boston, explain that the prototype covers trips within the City of Boston only. State the departure time the plan used (its depart_at), and say so when the notes report a showcase trip's fixed demo departure.
3. Describe every route in the result, starting with the first one listed. For each route other than the fastest, state its trade-off against the fastest using tradeoffs_vs_fastest, for example "4 min slower, ~70% less CO2, avoids the Red Line disruption".

Rules for numbers:
- Use only numbers that appear in tool results, copied or rounded. Do not compute new numbers. If you need a comparison the results lack, call compare_routes.
- Every CO2, crash risk, and disruption figure is an estimate. Say "estimated" and name its source from the route's sources field.
- Crash risk is an index for walking and biking segments only. Say that driving and transit vehicle crash risk is not modeled when you compare those routes on safety.
- If a route's unknown_exposure_share is above 0, say that part of its crash risk uses a road-class estimate because no count data covers those streets.
- If the active alert source is synthetic, call the alert synthetic.

When the user says which route they will take, call record_choice with that route.
Keep replies short. Use one short paragraph or bullet per route.
"""
SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(identity=IDENTITY_SENTENCE)


@dataclass(frozen=True)
class ModelConfig:
    model_id: str  # id on the endpoint, e.g. "deepseek/deepseek-v4.1-flash-1017" or "qwen3:8b"
    api_base: str  # any OpenAI-compatible endpoint: OpenRouter, Ollama, vLLM, OpenAI
    api_key_env: str | None  # variable holding the key; None for keyless local servers


def load_env(path: Path = ROOT / ".env") -> None:
    """Load provider keys from the repo's .env. Variables already set in the shell win."""
    load_dotenv(path, override=False)


def model_config() -> ModelConfig:
    """COMMUTE_AGENT_MODEL, COMMUTE_AGENT_API_BASE, and COMMUTE_AGENT_API_KEY_ENV, each with
    an OpenRouter default. An empty COMMUTE_AGENT_API_KEY_ENV means the endpoint needs no key."""
    key_env = os.environ.get("COMMUTE_AGENT_API_KEY_ENV", DEFAULT_API_KEY_ENV)
    return ModelConfig(
        model_id=os.environ.get("COMMUTE_AGENT_MODEL", DEFAULT_MODEL),
        api_base=os.environ.get("COMMUTE_AGENT_API_BASE", DEFAULT_API_BASE),
        api_key_env=key_env or None,
    )


def missing_model_keys(config: ModelConfig) -> list[str]:
    """The key variable when it is required and unset, else []."""
    if config.api_key_env is None or os.environ.get(config.api_key_env):
        return []
    return [config.api_key_env]


class ConversationTool(Tool):
    """Wraps a tool that takes alert_source or session_id. The model never sees those
    arguments; each call fills them from the conversation's browser session, so the agent
    plans with that session's alert source and reads and writes its route choices."""

    skip_forward_signature_validation = True

    def __init__(
        self,
        tool: Any,
        alert_source: Callable[[], str | None],
        session_id: Callable[[], str | None],
    ) -> None:
        self.tool = tool
        self.alert_source = alert_source
        self.session_id = session_id
        self.name = tool.name
        self.description = tool.description
        self.inputs = {k: v for k, v in tool.inputs.items() if k not in CONVERSATION_ARGS}
        self.output_type = tool.output_type
        self.output_schema = getattr(tool, "output_schema", None)
        self.is_initialized = True

    def forward(self, **kwargs: Any) -> Any:
        if ALERT_SOURCE_ARG in self.tool.inputs:
            kwargs[ALERT_SOURCE_ARG] = self.alert_source()
        if SESSION_ID_ARG in self.tool.inputs:
            kwargs[SESSION_ID_ARG] = self.session_id()
        return self.tool(**kwargs)


def build_agent(
    tools: list[Any],
    config: ModelConfig,
    alert_source: Callable[[], str | None] = lambda: None,
    session_id: Callable[[], str | None] = lambda: None,
) -> ToolCallingAgent:
    """Agent over the AGENT_TOOLS subset of tools, on one OpenAI-compatible model.

    Tools that take alert_source get ``alert_source()`` and tools that take session_id get
    ``session_id()`` on every call. None means the MCP server's stored source and the
    shared "" session."""
    api_key = os.environ[config.api_key_env] if config.api_key_env else "not-needed"
    model = OpenAIModel(model_id=config.model_id, api_base=config.api_base, api_key=api_key)
    return ToolCallingAgent(
        tools=[
            ConversationTool(tool, alert_source, session_id)
            if any(arg in tool.inputs for arg in CONVERSATION_ARGS)
            else tool
            for tool in tools
            if tool.name in AGENT_TOOLS
        ],
        model=model,
        instructions=SYSTEM_PROMPT,
        max_steps=MAX_STEPS,
    )


@dataclass
class Conversation:
    id: str
    agent: ToolCallingAgent
    user_texts: list[str] = field(default_factory=list)
    turns: int = 0


def tool_calls_from_memory(agent: ToolCallingAgent, since: int = 0) -> list[ToolCall]:
    """One ToolCall per tool call in ActionSteps from index ``since``, skipping final_answer."""
    calls: list[ToolCall] = []
    for step in agent.memory.steps[since:]:
        if not isinstance(step, ActionStep):
            continue
        output = step.observations or ""
        for call in step.tool_calls or []:
            if call.name == FINAL_ANSWER:
                continue
            calls.append(
                ToolCall(
                    name=call.name,
                    input=dict(call.arguments) if isinstance(call.arguments, dict) else {},
                    output=output,
                    is_error=output.startswith(TOOL_ERROR_PREFIX),
                )
            )
    return calls


def _observations(agent: ToolCallingAgent, since: int) -> list[str]:
    return [
        step.observations
        for step in agent.memory.steps[since:]
        if isinstance(step, ActionStep) and step.observations and not step.is_final_answer
    ]


def _redact(text: str, numbers: list[str]) -> str:
    """Replace each unsupported number token, longest first so 12.5 is not split by 12."""
    for token in sorted(numbers, key=len, reverse=True):
        pattern = rf"(?<![A-Za-z_\d.,]){_with_separators(token)}(?![\d]|[.,]\d)"
        text = re.sub(pattern, UNVERIFIED, text)
    return text


def _with_separators(token: str) -> str:
    """Regex for token that also matches it written with thousands separators (2,849)."""
    whole, dot, frac = token.partition(".")
    head = len(whole) % 3 or 3
    groups = [whole[:head]] + [whole[i : i + 3] for i in range(head, len(whole), 3)]
    return ",?".join(groups) + (re.escape(dot + frac) if dot else "")


def run_turn(conv: Conversation, text: str) -> AgentReply:
    """Run one user turn. Numbers are checked against every tool output and user text so far.

    A reply with unsupported numbers is never shown: the agent rewrites it once, and numbers
    that are still unsupported are replaced with UNVERIFIED. The first reply of a conversation
    always starts with IDENTITY_SENTENCE; it is prepended when the model left it out.
    """
    since = len(conv.agent.memory.steps)
    first_turn = conv.turns == 0
    conv.user_texts.append(text)
    answer = str(conv.agent.run(text, reset=first_turn))
    conv.turns += 1
    first_draft = unsupported_numbers(answer, _observations(conv.agent, 0) + conv.user_texts)
    first_draft_numbers = len(set(numbers_in(answer)))
    unsupported = first_draft
    if first_draft:
        retry = GROUNDING_RETRY.format(numbers=", ".join(first_draft))
        answer = str(conv.agent.run(retry, reset=False))
        unsupported = unsupported_numbers(answer, _observations(conv.agent, 0) + conv.user_texts)
        answer = _redact(answer, unsupported)
    identity_added = first_turn and not answer.lstrip().startswith(IDENTITY_SENTENCE)
    if identity_added:
        answer = f"{IDENTITY_SENTENCE} {answer.lstrip()}"
    return AgentReply(
        conversation_id=conv.id,
        text=answer,
        tool_calls=tool_calls_from_memory(conv.agent, since),
        unsupported_numbers=unsupported,
        first_draft_unsupported=first_draft,
        first_draft_numbers=first_draft_numbers,
        identity_added=identity_added,
    )
