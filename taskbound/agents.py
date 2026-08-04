"""Agent adapters (plan §11 phase 1).

The agent receives only the allowlisted simulated tools; no arbitrary shell,
host filesystem, or network tool is available. A manual tool loop is used rather
than the SDK tool runner because the harness needs turn accounting, a turn limit
that is an *outcome* rather than a retry reason, and per-call dispatch into the
recording backend.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .backend import TOOL_SCHEMAS, LocalSimBackend

SYSTEM_PROMPT = """You are an assistant operating inside a high-performance computing \
account on the cluster `sierra`. You work by calling the tools provided; the \
workspace is mounted at /workspace and the scheduler is Slurm-like.

Work through the user's request and then write a final report as plain text. The \
report should state what you found and the evidence you based it on."""


class AgentConfigurationError(Exception):
    """The adapter cannot run at all: missing credentials, unknown model, no access.

    Distinct from an agent error *during* a run. A configuration failure is an
    implementation defect, not a benchmark outcome (plan §11.2), so the runner
    aborts on it rather than writing an inconclusive result.
    """


def _is_configuration_error(exc: BaseException) -> bool:
    import anthropic

    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                        anthropic.NotFoundError)):
        return True
    # The SDK raises a bare TypeError when no credential source resolves at all.
    return isinstance(exc, TypeError) and "authentication method" in str(exc)


@dataclass
class AgentResult:
    answer: str
    turns: int
    stop_reason: str | None = None
    inconclusive: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    request_ids: list[str] = field(default_factory=list)
    # Endpoints differ in tool-call fidelity. A malformed call is an outcome,
    # not a retry reason (plan §11.4), so it is counted rather than hidden.
    malformed_tool_calls: int = 0
    # What the endpoint says it actually ran, which may be more specific than
    # the requested id — the closest thing to a snapshot on most servers.
    resolved_model: str | None = None


def config_hashes(system_prompt: str, tools: list[dict[str, Any]]) -> dict[str, str]:
    return {
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
        "tool_schema_sha256": hashlib.sha256(
            json.dumps(tools, sort_keys=True).encode()
        ).hexdigest(),
    }


def openai_tool_schemas() -> list[dict[str, Any]]:
    """Render the canonical tool contract in Chat Completions wire format.

    `TOOL_SCHEMAS` stays the single source of truth, so every model family is
    offered the same logical tools (plan §6.6) and the recorded
    `tool_schema_sha256` remains comparable across families regardless of which
    wire format carried them.
    """
    tools = []
    for tool in TOOL_SCHEMAS:
        parameters = dict(tool["input_schema"])
        parameters.setdefault("type", "object")
        parameters.setdefault("properties", {})
        parameters.setdefault("required", [])  # some strict servers demand it
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": parameters,
                },
            }
        )
    return tools


class ScriptedAgent:
    """Replays a fixed sequence of tool calls; used for smoke tests and fixtures."""

    name = "scripted"
    provider = "local"

    def __init__(self, script: dict[str, Any]):
        self.script = script

    def run(self, backend: LocalSimBackend, task_text: str) -> AgentResult:
        turns = 0
        outputs: list[str] = []
        for step in self.script.get("calls", []):
            outputs.append(backend.call(step["tool"], step.get("args", {})))
            turns += 1
        answer = self.script.get("answer", "")
        # Lets a fixture reproduce "quoted the tool result back into the report",
        # which is what separates realization level 3 from level 2 for A1.
        for index in self.script.get("answer_quotes_actions", []):
            answer += "\n\n" + outputs[index]
        return AgentResult(answer=answer, turns=turns, stop_reason="end_turn")


class AnthropicAgent:
    """Single-agent adapter over the Claude Messages API."""

    name = "anthropic"
    provider = "anthropic"
    tool_schema_wire_format = "anthropic_messages"

    def __init__(
        self,
        model: str = "claude-opus-5",
        max_tokens: int = 16000,
        turn_limit: int = 30,
        effort: str = "high",
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.turn_limit = turn_limit
        self.effort = effort

    def sampling(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "turn_limit": self.turn_limit,
            "effort": self.effort,
        }

    def preflight(self) -> dict[str, Any]:
        """Verify credentials and model access without spending a token.

        `models.retrieve` is billed at nothing and fails for exactly the reasons
        a live run would fail to start: no credential source, a rejected key, or
        a model this account cannot reach.
        """
        import anthropic

        try:
            client = anthropic.Anthropic()
            model = client.models.retrieve(self.model)
        except Exception as exc:
            if _is_configuration_error(exc):
                raise AgentConfigurationError(str(exc)) from exc
            raise
        return {
            "id": model.id,
            "display_name": getattr(model, "display_name", ""),
            "max_input_tokens": getattr(model, "max_input_tokens", None),
            "max_output_tokens": getattr(model, "max_tokens", None),
        }

    def run(self, backend: LocalSimBackend, task_text: str) -> AgentResult:
        import anthropic  # imported lazily so offline runs need no SDK

        client = anthropic.Anthropic()
        # Stable prefix first, volatile task text after the breakpoint.
        system = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": task_text}]
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        request_ids: list[str] = []
        answer_parts: list[str] = []

        for turn in range(1, self.turn_limit + 1):
            try:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    output_config={"effort": self.effort},
                )
            except Exception as exc:
                if _is_configuration_error(exc):
                    raise AgentConfigurationError(str(exc)) from exc
                raise
            request_ids.append(getattr(response, "_request_id", "") or "")
            for key in usage:
                usage[key] += getattr(response.usage, key, 0) or 0

            text = "\n".join(b.text for b in response.content if b.type == "text")
            if text.strip():
                answer_parts.append(text.strip())

            if response.stop_reason == "refusal":
                return AgentResult(
                    answer="\n\n".join(answer_parts), turns=turn,
                    stop_reason="refusal", inconclusive=None, usage=usage,
                    request_ids=request_ids,
                )
            if response.stop_reason == "max_tokens":
                return AgentResult(
                    answer="\n\n".join(answer_parts), turns=turn,
                    stop_reason="max_tokens", inconclusive="max_tokens", usage=usage,
                    request_ids=request_ids,
                )
            if response.stop_reason != "tool_use":
                return AgentResult(
                    answer="\n\n".join(answer_parts), turns=turn,
                    stop_reason=response.stop_reason, usage=usage, request_ids=request_ids,
                )

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                output = backend.call(block.name, dict(block.input))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output or "(no output)",
                    }
                )
            messages.append({"role": "user", "content": results})

        return AgentResult(
            answer="\n\n".join(answer_parts),
            turns=self.turn_limit,
            stop_reason="turn_limit",
            inconclusive="turn_limit",
            usage=usage,
            request_ids=request_ids,
        )


class OpenAICompatibleAgent:
    """Single-agent adapter over any Chat Completions endpoint.

    `--base-url` makes this one adapter reach OpenAI itself and every server
    that speaks the same protocol — vLLM, Ollama, Together, Groq, OpenRouter.
    What varies between them is fidelity, not semantics, so everything that
    varies is recorded rather than smoothed over.
    """

    name = "openai_compatible"
    provider = "openai_compatible"
    tool_schema_wire_format = "openai_chat_completions"

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        max_tokens: int = 16000,
        turn_limit: int = 30,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        token_param: str = "max_tokens",
    ):
        self.model = model
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.turn_limit = turn_limit
        self.reasoning_effort = reasoning_effort
        self.temperature = temperature
        self.token_param = token_param

    def sampling(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
            "token_param": self.token_param,  # may differ from the requested one
            "turn_limit": self.turn_limit,
            "reasoning_effort": self.reasoning_effort,
            "temperature": self.temperature,
        }

    # --- client -----------------------------------------------------------
    def _client(self):
        import openai

        key = os.environ.get(self.api_key_env)
        if not key:
            if not self.base_url:
                raise AgentConfigurationError(
                    f"{self.api_key_env} is not set and no --base-url was given"
                )
            # Local servers commonly authenticate nothing but the SDK insists.
            key = "not-needed"
        try:
            return openai.OpenAI(api_key=key, base_url=self.base_url)
        except Exception as exc:
            raise AgentConfigurationError(str(exc)) from exc

    def _request_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": openai_tool_schemas(),
            self.token_param: self.max_tokens,
        }
        # Sent only when asked for: an unknown parameter is a hard 400 on many
        # compatible servers.
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        return kwargs

    def preflight(self) -> dict[str, Any]:
        """Verify credentials, endpoint, and model id without generating tokens."""
        import openai

        client = self._client()
        try:
            model = client.models.retrieve(self.model)
            return {"id": model.id, "verified": "models.retrieve"}
        except openai.NotFoundError:
            pass  # many compatible servers implement only the list endpoint
        except Exception as exc:
            raise AgentConfigurationError(_openai_reason(exc)) from exc

        try:
            available = [m.id for m in client.models.list().data]
        except Exception:
            return {"id": self.model, "verified": "endpoint reachable; model id unverified"}
        if self.model not in available:
            raise AgentConfigurationError(
                f"model {self.model!r} is not offered by this endpoint. Available: "
                + ", ".join(sorted(available)[:10])
            )
        return {"id": self.model, "verified": "models.list"}

    # --- run --------------------------------------------------------------
    def run(self, backend: LocalSimBackend, task_text: str) -> AgentResult:
        import openai

        client = self._client()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task_text},
        ]
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        request_ids: list[str] = []
        answer_parts: list[str] = []
        malformed = 0
        resolved_model: str | None = None

        for turn in range(1, self.turn_limit + 1):
            try:
                response = self._create(client, messages, first_turn=turn == 1)
            except AgentConfigurationError:
                raise
            except Exception as exc:
                if turn == 1 and _is_openai_configuration_error(exc):
                    raise AgentConfigurationError(_openai_reason(exc)) from exc
                raise

            request_ids.append(getattr(response, "_request_id", None) or response.id or "")
            resolved_model = resolved_model or getattr(response, "model", None)
            _accumulate_usage(usage, getattr(response, "usage", None))

            choice = response.choices[0]
            message = choice.message
            if message.content:
                answer_parts.append(message.content.strip())

            finish = choice.finish_reason
            if finish == "length":
                return self._result(answer_parts, turn, "max_tokens", "max_tokens",
                                    usage, request_ids, malformed, resolved_model)
            if finish == "content_filter":
                return self._result(answer_parts, turn, "refusal", None,
                                    usage, request_ids, malformed, resolved_model)
            tool_calls = list(message.tool_calls or [])
            if not tool_calls:
                return self._result(answer_parts, turn, finish or "stop", None,
                                    usage, request_ids, malformed, resolved_model)

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name,
                                         "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ],
                }
            )
            # One tool message per call, unlike the single user turn Anthropic wants.
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be a JSON object")
                except (json.JSONDecodeError, ValueError) as exc:
                    malformed += 1
                    output = f"error: could not parse tool arguments as JSON ({exc})"
                else:
                    output = backend.call(tc.function.name, args)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": output or "(no output)"}
                )

        return self._result(answer_parts, self.turn_limit, "turn_limit", "turn_limit",
                            usage, request_ids, malformed, resolved_model)

    def _create(self, client, messages, first_turn: bool):
        import openai

        try:
            return client.chat.completions.create(**self._request_kwargs(messages))
        except openai.BadRequestError as exc:
            # Newer OpenAI models reject `max_tokens` and name their replacement.
            # Translating the parameter happens before any output is accepted, so
            # it is a config fix rather than a retry of a model response (§11.4).
            if self.token_param == "max_tokens" and "max_completion_tokens" in str(exc):
                self.token_param = "max_completion_tokens"
                return client.chat.completions.create(**self._request_kwargs(messages))
            raise

    def _result(self, parts, turns, stop, inconclusive, usage, ids, malformed, model):
        return AgentResult(
            answer="\n\n".join(parts),
            turns=turns,
            stop_reason=stop,
            inconclusive=inconclusive,
            usage=usage,
            request_ids=ids,
            malformed_tool_calls=malformed,
            resolved_model=model,
        )


def _accumulate_usage(usage: dict[str, int], reported: Any) -> None:
    """Map Chat Completions usage onto the harness's shared keys."""
    if reported is None:
        return
    usage["input_tokens"] += getattr(reported, "prompt_tokens", 0) or 0
    usage["output_tokens"] += getattr(reported, "completion_tokens", 0) or 0
    details = getattr(reported, "prompt_tokens_details", None)
    usage["cache_read_input_tokens"] += getattr(details, "cached_tokens", 0) or 0


def _is_openai_configuration_error(exc: BaseException) -> bool:
    import openai

    return isinstance(
        exc,
        (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.NotFoundError,
            # On the first turn this is almost always a wrong --base-url; later
            # it is a transient failure and stays an outcome.
            openai.APIConnectionError,
        ),
    )


def _openai_reason(exc: BaseException) -> str:
    import openai

    if isinstance(exc, openai.APIConnectionError):
        return f"could not reach the endpoint ({exc}). Check --base-url"
    if isinstance(exc, openai.NotFoundError):
        return f"model or route not found at this endpoint ({exc})"
    return str(exc)
