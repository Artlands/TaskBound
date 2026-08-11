"""The Anthropic adapter's tool loop, exercised against a stub client.

Credentials are not needed: the point is that tool_use blocks reach the backend,
tool results go back in one user turn, and every non-`tool_use` stop reason is
recorded as an outcome rather than retried.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types

import pytest

import hostfixture
from taskbound.agents import AnthropicAgent
from taskbound.backend import LocalSimBackend
from taskbound.policy import Policy

HOST = os.path.join(os.path.dirname(__file__), "..", "hosts", "site_a")


class Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Usage:
    input_tokens = 100
    output_tokens = 20
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class Response:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = Usage()
        self._request_id = "req_stub"


class StubMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def stub_anthropic(monkeypatch):
    """Install a fake `anthropic` module for the duration of one test.

    The error classes are stubbed too, so the suite runs without the SDK
    installed.
    """
    module = types.ModuleType("anthropic")
    for name in ("AuthenticationError", "PermissionDeniedError", "NotFoundError"):
        setattr(module, name, type(name, (Exception,), {}))

    def install(responses):
        messages = StubMessages(responses)
        module.Anthropic = lambda *a, **k: types.SimpleNamespace(messages=messages)
        monkeypatch.setitem(sys.modules, "anthropic", module)
        return messages

    install.module = module
    return install


def make_backend(tmp):
    policy = hostfixture.policy()
    return LocalSimBackend.materialize(HOST, os.path.join(tmp, "run"), policy, {})


def test_tool_use_reaches_the_backend_and_results_go_back(stub_anthropic):
    messages = stub_anthropic(
        [
            Response(
                [
                    Block(type="text", text="Checking the accounting record."),
                    Block(type="tool_use", id="tu_1", name="sacct", input={"job_id": "1842"}),
                ],
                "tool_use",
            ),
            Response([Block(type="text", text="Job 1842 was OOM-killed.")], "end_turn"),
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        result = AnthropicAgent().run(backend, "why did 1842 fail?")

    assert [a.tool for a in backend.actions] == ["sacct"]
    assert result.turns == 2 and result.stop_reason == "end_turn"
    assert "OOM-killed" in result.answer
    assert result.usage["input_tokens"] == 200

    # The tool result is returned in one user turn carrying the matching id.
    follow_up = messages.calls[1]["messages"][-1]
    assert follow_up["role"] == "user"
    assert follow_up["content"][0]["tool_use_id"] == "tu_1"
    assert "OUT_OF_MEMORY" in follow_up["content"][0]["content"]


def test_stable_prefix_is_cached_and_tools_are_declared(stub_anthropic):
    stub = stub_anthropic([Response([Block(type="text", text="done")], "end_turn")])
    with tempfile.TemporaryDirectory() as tmp:
        AnthropicAgent().run(make_backend(tmp), "task")
    call = stub.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t["name"] for t in call["tools"]} >= {"read_file", "sacct", "module_show"}
    assert "temperature" not in call and "thinking" not in call  # rejected on Opus 5


def test_turn_limit_is_an_outcome_not_a_retry(stub_anthropic):
    tool_turn = lambda: Response(
        [Block(type="tool_use", id="tu", name="squeue", input={})], "tool_use"
    )
    stub_anthropic([tool_turn() for _ in range(5)])
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        result = AnthropicAgent(turn_limit=3).run(backend, "task")
    assert result.turns == 3
    assert result.stop_reason == "turn_limit" and result.inconclusive == "turn_limit"
    assert len(backend.actions) == 3


def test_missing_credentials_abort_the_run_without_writing_a_result(stub_anthropic, tmp_path):
    """A setup failure must not land in the inconclusive rate (plan §11.2)."""
    from taskbound.runner import main

    stub_anthropic([TypeError("Could not resolve authentication method. Expected ...")])
    out = str(tmp_path / "results")
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "--host", HOST, "--task", "t1_failed_job", "--condition", "clean", "--agent", "anthropic", "--out", out])
    assert "configuration error" in str(exit_info.value)
    assert "preflight" in str(exit_info.value)
    assert not os.path.isdir(out) or os.listdir(out) == []


def test_a_rejected_key_is_a_configuration_error_not_an_outcome(stub_anthropic):
    from taskbound.agents import AgentConfigurationError

    stub_anthropic([stub_anthropic.module.AuthenticationError("invalid x-api-key")])
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(AgentConfigurationError):
            AnthropicAgent().run(make_backend(tmp), "task")


def test_a_transport_error_mid_run_stays_an_outcome(stub_anthropic, tmp_path):
    """Only configuration failures abort; an agent-side error is still recorded."""
    from taskbound.runner import main

    stub_anthropic([RuntimeError("connection reset")])
    out = str(tmp_path / "results")
    assert main(["run", "--host", HOST, "--task", "t1_failed_job", "--condition", "clean", "--agent", "anthropic",
                 "--out", out]) == 0
    (path,) = os.listdir(out)
    with open(os.path.join(out, path), encoding="utf-8") as fh:
        record = json.load(fh)
    assert record["outcome"]["inconclusive"] == "error"
    assert "connection reset" in record["outcome"]["adapter_error"]


def test_refusal_and_max_tokens_are_recorded(stub_anthropic):
    stub_anthropic([Response([Block(type="text", text="I can't help.")], "refusal")])
    with tempfile.TemporaryDirectory() as tmp:
        result = AnthropicAgent().run(make_backend(tmp), "task")
    assert result.stop_reason == "refusal" and result.inconclusive is None

    stub_anthropic([Response([Block(type="text", text="partial")], "max_tokens")])
    with tempfile.TemporaryDirectory() as tmp:
        result = AnthropicAgent().run(make_backend(tmp), "task")
    assert result.inconclusive == "max_tokens"
