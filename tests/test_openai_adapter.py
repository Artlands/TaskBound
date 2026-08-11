"""The OpenAI-compatible adapter, exercised against a stub client.

No credentials and no SDK needed: the stub supplies both the client and the
error classes, so this runs anywhere the rest of the suite does.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types

import pytest

import hostfixture
from taskbound.agents import OpenAICompatibleAgent, openai_tool_schemas
from taskbound.backend import TOOL_SCHEMAS, LocalSimBackend

HOST = os.path.join(os.path.dirname(__file__), "..", "hosts", "site_a")


def call(id_, name, arguments):
    return types.SimpleNamespace(
        id=id_, type="function",
        function=types.SimpleNamespace(name=name, arguments=arguments),
    )


def completion(content=None, tool_calls=None, finish_reason="stop", model="stub-model",
               prompt_tokens=100, completion_tokens=20, cached_tokens=0):
    message = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    return types.SimpleNamespace(
        id="chatcmpl-stub",
        model=model,
        choices=[types.SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=types.SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=types.SimpleNamespace(cached_tokens=cached_tokens),
        ),
    )


class StubCompletions:
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
def stub_openai(monkeypatch):
    module = types.ModuleType("openai")
    module.__version__ = "4.5.6-test"
    for name in ("AuthenticationError", "PermissionDeniedError", "NotFoundError",
                 "APIConnectionError", "BadRequestError", "OpenAIError"):
        setattr(module, name, type(name, (Exception,), {}))

    state: dict = {}

    def install(responses, models=None):
        completions = StubCompletions(responses)
        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=completions),
            models=types.SimpleNamespace(
                retrieve=lambda m: (_ for _ in ()).throw(module.NotFoundError("no such route")),
                list=lambda: types.SimpleNamespace(
                    data=[types.SimpleNamespace(id=m) for m in (models or [])]
                ),
            ),
        )
        module.OpenAI = lambda **kw: (state.update(kw), client)[1]
        monkeypatch.setitem(sys.modules, "openai", module)
        return completions

    install.module = module
    install.client_kwargs = state
    return install


def make_backend(tmp):
    policy = hostfixture.policy()
    return LocalSimBackend.materialize(HOST, os.path.join(tmp, "run"), policy, {})


def agent(**kw):
    kw.setdefault("model", "stub-model")
    kw.setdefault("api_key_env", "TB_TEST_KEY")
    kw.setdefault("base_url", "http://localhost:8000/v1")
    return OpenAICompatibleAgent(**kw)


# --- schema translation -------------------------------------------------
def test_tool_contract_is_translated_not_redefined():
    translated = openai_tool_schemas()
    assert len(translated) == len(TOOL_SCHEMAS)
    assert {t["function"]["name"] for t in translated} == {t["name"] for t in TOOL_SCHEMAS}
    read = next(t for t in translated if t["function"]["name"] == "read_file")
    assert read["type"] == "function"
    assert read["function"]["parameters"] == TOOL_SCHEMAS[1]["input_schema"]
    # Parameterless tools still carry a well-formed object schema.
    squeue = next(t for t in translated if t["function"]["name"] == "squeue")
    assert squeue["function"]["parameters"] == {"type": "object", "properties": {}, "required": []}


# --- the loop -----------------------------------------------------------
def test_tool_calls_reach_the_backend_and_get_one_tool_message_each(stub_openai):
    stub = stub_openai(
        [
            completion(
                content="Checking the record.",
                tool_calls=[call("c1", "sacct", '{"job_id": "1842"}'),
                            call("c2", "read_file", '{"path": "/workspace/logs/job_1842.err"}')],
                finish_reason="tool_calls",
            ),
            completion(content="Job 1842 was OOM-killed.", finish_reason="stop"),
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        result = agent().run(backend, "why did 1842 fail?")

    assert [a.tool for a in backend.actions] == ["sacct", "read_file"]
    assert result.stop_reason == "stop" and result.turns == 2
    assert "OOM-killed" in result.answer
    assert result.resolved_model == "stub-model"
    assert result.resolved_models == ["stub-model", "stub-model"]

    # Chat Completions wants one `tool` message per call, keyed by id.
    sent = stub.calls[1]["messages"]
    assert sent[0]["role"] == "system" and sent[1]["role"] == "user"
    assert sent[2]["role"] == "assistant" and len(sent[2]["tool_calls"]) == 2
    assert [m["tool_call_id"] for m in sent[3:5]] == ["c1", "c2"]
    assert "OUT_OF_MEMORY" in sent[3]["content"]


def test_usage_is_mapped_onto_the_shared_keys(stub_openai):
    stub_openai([completion(content="done", prompt_tokens=250, completion_tokens=40,
                            cached_tokens=200)])
    with tempfile.TemporaryDirectory() as tmp:
        result = agent().run(make_backend(tmp), "task")
    assert result.usage["input_tokens"] == 250
    assert result.usage["output_tokens"] == 40
    assert result.usage["cache_read_input_tokens"] == 200


def test_malformed_tool_arguments_are_counted_not_fatal(stub_openai):
    """A malformed call is an outcome, and the model gets a chance to recover."""
    stub = stub_openai(
        [
            completion(tool_calls=[call("c1", "read_file", "{not json")],
                       finish_reason="tool_calls"),
            completion(tool_calls=[call("c2", "read_file",
                                        '{"path": "/workspace/logs/job_1842.err"}')],
                       finish_reason="tool_calls"),
            completion(content="recovered", finish_reason="stop"),
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        result = agent().run(backend, "task")

    assert result.malformed_tool_calls == 1
    assert [a.tool for a in backend.actions] == ["read_file"]  # only the valid one ran
    assert "could not parse tool arguments" in stub.calls[1]["messages"][3]["content"]
    assert result.stop_reason == "stop"


def test_non_object_arguments_are_also_rejected(stub_openai):
    stub_openai([completion(tool_calls=[call("c1", "read_file", '"a string"')],
                            finish_reason="tool_calls"),
                 completion(content="done", finish_reason="stop")])
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        result = agent().run(backend, "task")
    assert result.malformed_tool_calls == 1 and backend.actions == []


def test_finish_reasons_map_to_outcomes(stub_openai):
    stub_openai([completion(content="partial", finish_reason="length")])
    with tempfile.TemporaryDirectory() as tmp:
        assert agent().run(make_backend(tmp), "task").inconclusive == "max_tokens"

    stub_openai([completion(content="", finish_reason="content_filter")])
    with tempfile.TemporaryDirectory() as tmp:
        result = agent().run(make_backend(tmp), "task")
    assert result.stop_reason == "refusal" and result.inconclusive is None


def test_turn_limit_is_an_outcome(stub_openai):
    loop = [completion(tool_calls=[call(f"c{i}", "squeue", "{}")], finish_reason="tool_calls")
            for i in range(5)]
    stub_openai(loop)
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        result = agent(turn_limit=3).run(backend, "task")
    assert result.inconclusive == "turn_limit" and len(backend.actions) == 3


# --- request shaping ----------------------------------------------------
def test_optional_parameters_are_omitted_unless_asked_for(stub_openai):
    stub = stub_openai([completion(content="done")])
    with tempfile.TemporaryDirectory() as tmp:
        agent().run(make_backend(tmp), "task")
    sent = stub.calls[0]
    assert "reasoning_effort" not in sent and "temperature" not in sent
    assert sent["max_tokens"] == 16000

    stub = stub_openai([completion(content="done")])
    with tempfile.TemporaryDirectory() as tmp:
        agent(reasoning_effort="high", temperature=0.2).run(make_backend(tmp), "task")
    assert stub.calls[0]["reasoning_effort"] == "high"
    assert stub.calls[0]["temperature"] == 0.2


def test_token_parameter_switches_when_the_server_demands_it(stub_openai):
    module = stub_openai.module
    stub = stub_openai(
        [
            module.BadRequestError(
                "Unsupported parameter: 'max_tokens' is not supported. Use 'max_completion_tokens'"
            ),
            completion(content="done"),
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        a = agent()
        result = a.run(make_backend(tmp), "task")
    assert "max_tokens" in stub.calls[0] and "max_completion_tokens" in stub.calls[1]
    assert a.sampling()["token_param"] == "max_completion_tokens"  # recorded in the result
    assert result.retry_history == [{
        "kind": "parameter_negotiation",
        "from": "max_tokens",
        "to": "max_completion_tokens",
    }]


def test_base_url_and_key_reach_the_client(stub_openai, monkeypatch):
    stub_openai([completion(content="done")])
    monkeypatch.setenv("TB_TEST_KEY", "sk-test")
    with tempfile.TemporaryDirectory() as tmp:
        agent(base_url="http://example.invalid/v1").run(make_backend(tmp), "task")
    assert stub_openai.client_kwargs["base_url"] == "http://example.invalid/v1"
    assert stub_openai.client_kwargs["api_key"] == "sk-test"
    assert stub_openai.client_kwargs["max_retries"] == 0


def test_a_local_endpoint_needs_no_key(stub_openai, monkeypatch):
    monkeypatch.delenv("TB_TEST_KEY", raising=False)
    stub_openai([completion(content="done")])
    with tempfile.TemporaryDirectory() as tmp:
        agent().run(make_backend(tmp), "task")
    assert stub_openai.client_kwargs["api_key"] == "not-needed"


def test_a_missing_key_without_a_base_url_is_a_configuration_error(stub_openai, monkeypatch):
    from taskbound.agents import AgentConfigurationError

    monkeypatch.delenv("TB_TEST_KEY", raising=False)
    stub_openai([completion(content="done")])
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(AgentConfigurationError, match="TB_TEST_KEY"):
            agent(base_url=None).run(make_backend(tmp), "task")


# --- configuration failures --------------------------------------------
def test_an_unreachable_base_url_aborts_without_writing_a_result(stub_openai, tmp_path):
    from taskbound.runner import main

    module = stub_openai.module
    stub_openai([module.APIConnectionError("Connection refused")])
    out = str(tmp_path / "results")
    with pytest.raises(SystemExit) as info:
        main(["run", "--host", HOST, "--task", "t1_failed_job", "--condition", "clean", "--agent", "openai_compatible",
              "--model", "stub-model", "--base-url", "http://localhost:9/v1",
              "--api-key-env", "TB_TEST_KEY", "--out", out])
    assert "could not reach the endpoint" in str(info.value)
    assert "--base-url" in str(info.value)
    assert not os.path.isdir(out) or os.listdir(out) == []


def test_a_connection_failure_after_the_first_turn_stays_an_outcome(stub_openai, tmp_path):
    """Only the first turn implies a misconfigured endpoint; later is transient."""
    from taskbound.runner import main

    module = stub_openai.module
    stub_openai([
        completion(tool_calls=[call("c1", "squeue", "{}")], finish_reason="tool_calls"),
        module.APIConnectionError("Connection reset"),
    ])
    out = str(tmp_path / "results")
    assert main(["run", "--host", HOST, "--task", "t1_failed_job", "--condition", "clean", "--agent", "openai_compatible",
                 "--model", "stub-model", "--base-url", "http://localhost:8000/v1",
                 "--api-key-env", "TB_TEST_KEY", "--out", out]) == 0
    (path,) = os.listdir(out)
    with open(os.path.join(out, path), encoding="utf-8") as fh:
        record = json.load(fh)
    assert record["outcome"]["inconclusive"] == "error"
    assert record["outcome"]["request_ids"] == ["chatcmpl-stub"]
    assert record["agent"]["resolved_models"] == ["stub-model"]
    assert "Connection reset" in record["outcome"]["adapter_error"]


def test_a_later_role_configuration_error_is_an_outcome(stub_openai):
    from taskbound.agents import Role, TurnBudget

    module = stub_openai.module
    stub_openai([
        completion(content="delegate", model="snapshot-a"),
        module.AuthenticationError("worker access revoked"),
    ])
    budget = TurnBudget(3)
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        first = agent().run(
            backend, "task", role=Role(actor="planner"), budget=budget
        )
        second = agent().run(
            backend, first.answer, role=Role(actor="worker"), budget=budget
        )

    assert first.resolved_models == ["snapshot-a"]
    assert second.inconclusive == "error"
    assert second.request_ids == []
    assert "worker access revoked" in second.adapter_error


def test_preflight_falls_back_to_the_list_endpoint(stub_openai, capsys):
    from taskbound.runner import main

    stub_openai([], models=["stub-model", "other-model"])
    assert main(["preflight", "--agent", "openai_compatible", "--model", "stub-model",
                 "--base-url", "http://localhost:8000/v1", "--api-key-env", "TB_TEST_KEY"]) == 0
    out = capsys.readouterr().out
    assert "OK:" in out and "models.list" in out

    stub_openai([], models=["other-model"])
    assert main(["preflight", "--agent", "openai_compatible", "--model", "stub-model",
                 "--base-url", "http://localhost:8000/v1", "--api-key-env", "TB_TEST_KEY"]) == 1
    assert "not offered by this endpoint" in capsys.readouterr().out


# --- provenance ---------------------------------------------------------
def test_the_result_records_which_wire_format_carried_the_contract(stub_openai, tmp_path):
    from taskbound.runner import main

    stub_openai([completion(content="Job 1842 hit an out-of-memory kill; 8G requested, "
                                    "9.83G observed.")])
    out = str(tmp_path / "results")
    assert main(["run", "--host", HOST, "--task", "t1_failed_job", "--condition", "clean", "--agent", "openai_compatible",
                 "--model", "stub-model", "--base-url", "http://localhost:8000/v1",
                 "--api-key-env", "TB_TEST_KEY", "--out", out]) == 0
    (path,) = os.listdir(out)
    with open(os.path.join(out, path), encoding="utf-8") as fh:
        record = json.load(fh)

    agent_block = record["agent"]
    assert agent_block["provider"] == "openai_compatible"
    assert agent_block["tool_schema_wire_format"] == "openai_chat_completions"
    assert agent_block["resolved_model"] == "stub-model"
    assert agent_block["resolved_models"] == ["stub-model"]
    assert agent_block["sampling"]["base_url"] == "http://localhost:8000/v1"
    assert agent_block["sdk"] == {"package": "openai", "version": "4.5.6-test"}
    assert agent_block["transport_retry_policy"] == {"max_retries": 0}
    assert record["outcome"]["malformed_tool_calls"] == 0
    # The contract hash is of the canonical tools, so it is comparable across
    # families even though the wire format differs.
    assert agent_block["tool_schema_sha256"]
