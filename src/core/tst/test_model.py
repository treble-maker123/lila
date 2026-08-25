"""Unit tests for lila.model. No daemon, no network."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from lila.model import (
    DEFAULT_OLLAMA_HOST,
    GenerateEvent,
    GenerateOptions,
    Message,
    Model,
    ModelError,
    OllamaModel,
    TextChunk,
    ThinkingChunk,
    Usage,
)

# region fixtures

TransportFactory = Callable[..., httpx.MockTransport]
ModelFactory = Callable[..., OllamaModel]


class ScriptedModel(Model):
    """Backend that replays a fixed event list, for exercising inherited behavior."""

    def __init__(self, events: list[GenerateEvent]) -> None:
        self._events = events

    @property
    def name(self) -> str:
        return "scripted"

    async def generate(
        self,
        messages: list[Message],
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[GenerateEvent]:
        for event in self._events:
            yield event


@pytest.fixture
def ndjson_transport() -> TransportFactory:
    """Build a transport serving ``lines`` as ollama's /api/chat NDJSON body."""

    def build(lines: list[str], status_code: int = 200) -> httpx.MockTransport:
        body = "".join(f"{line}\n" for line in lines).encode()
        return httpx.MockTransport(lambda request: httpx.Response(status_code, content=body))

    return build


@pytest.fixture
async def ollama_with() -> AsyncIterator[ModelFactory]:
    """Build a backend wired to a mock transport. Injected clients close on teardown."""
    clients: list[httpx.AsyncClient] = []

    def build(
        transport: httpx.MockTransport,
        model: str = "test-model",
        host: str = DEFAULT_OLLAMA_HOST,
    ) -> OllamaModel:
        client = httpx.AsyncClient(transport=transport)
        clients.append(client)
        return OllamaModel(model, host=host, client=client)

    yield build

    for client in clients:
        await client.aclose()


@pytest.fixture
def model() -> OllamaModel:
    """Bare backend, for tests that inspect it without issuing a request."""
    return OllamaModel("test-model")


# endregion

# region _parse_chat_line


def test_parse_chat_line__returns_text_chunk_when_message_has_content() -> None:
    # prepare
    line = json.dumps({"message": {"role": "assistant", "content": "hello"}, "done": False})

    # act
    events = OllamaModel._parse_chat_line(line)

    # verify
    assert events == [TextChunk(text="hello")]


def test_parse_chat_line__returns_thinking_chunk_when_message_has_thinking() -> None:
    # prepare
    line = json.dumps({"message": {"role": "assistant", "thinking": "hmm"}, "done": False})

    # act
    events = OllamaModel._parse_chat_line(line)

    # verify
    assert events == [ThinkingChunk(text="hmm")]


def test_parse_chat_line__orders_thinking_before_text_when_both_present() -> None:
    # prepare
    line = json.dumps({"message": {"thinking": "hmm", "content": "hi"}, "done": False})

    # act
    events = OllamaModel._parse_chat_line(line)

    # verify
    assert events == [ThinkingChunk(text="hmm"), TextChunk(text="hi")]


def test_parse_chat_line__returns_usage_when_payload_is_done() -> None:
    # prepare
    line = json.dumps(
        {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 17,
            "eval_count": 5,
        }
    )

    # act
    events = OllamaModel._parse_chat_line(line)

    # verify
    assert events == [Usage(prompt_tokens=17, completion_tokens=5, done_reason="stop")]


def test_parse_chat_line__defaults_token_counts_to_zero_when_absent() -> None:
    # prepare
    line = json.dumps({"done": True})

    # act
    events = OllamaModel._parse_chat_line(line)

    # verify
    assert events == [Usage(prompt_tokens=0, completion_tokens=0, done_reason=None)]


def test_parse_chat_line__returns_no_events_when_message_is_empty() -> None:
    # prepare
    line = json.dumps({"message": {"content": ""}, "done": False})

    # act
    events = OllamaModel._parse_chat_line(line)

    # verify
    assert events == []


def test_parse_chat_line__raises_model_error_when_payload_reports_an_error() -> None:
    # prepare
    line = json.dumps({"error": "model not found"})

    # act / verify
    with pytest.raises(ModelError, match="model not found"):
        OllamaModel._parse_chat_line(line)


def test_parse_chat_line__raises_model_error_when_line_is_malformed_json() -> None:
    # prepare
    line = "{not json"

    # act / verify
    with pytest.raises(ModelError, match="malformed line"):
        OllamaModel._parse_chat_line(line)


# endregion

# region _request_body


def test_request_body__streams_with_zero_temperature_when_options_omitted(
    model: OllamaModel,
) -> None:
    # prepare
    messages = [Message(role="user", content="hi")]

    # act
    body = model._request_body(messages, None)

    # verify
    assert body["model"] == "test-model"
    assert body["stream"] is True
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["options"] == {"temperature": 0.0}


def test_request_body__omits_unset_knobs_so_backend_defaults_win(model: OllamaModel) -> None:
    # act
    body = model._request_body([], GenerateOptions())

    # verify
    options = body["options"]
    assert isinstance(options, dict)
    assert set(options) == {"temperature"}
    assert "format" not in body
    assert "think" not in body


def test_request_body__maps_sampling_options_to_ollama_names(model: OllamaModel) -> None:
    # prepare
    options = GenerateOptions(
        temperature=0.7,
        seed=42,
        max_tokens=128,
        stop=("STOP",),
        context_length=8192,
    )

    # act
    body = model._request_body([], options)

    # verify
    assert body["options"] == {
        "temperature": 0.7,
        "seed": 42,
        "num_predict": 128,
        "stop": ["STOP"],
        "num_ctx": 8192,
    }


def test_request_body__sets_format_when_json_schema_is_given(model: OllamaModel) -> None:
    # prepare
    schema = {"type": "object", "properties": {"route": {"type": "string"}}}

    # act
    body = model._request_body([], GenerateOptions(json_schema=schema))

    # verify
    assert body["format"] == schema


def test_request_body__sets_think_when_explicitly_disabled(model: OllamaModel) -> None:
    # act
    body = model._request_body([], GenerateOptions(think=False))

    # verify
    assert body["think"] is False


# endregion

# region generate


async def test_generate__yields_events_in_stream_order_when_backend_streams(
    ollama_with: ModelFactory,
    ndjson_transport: TransportFactory,
) -> None:
    # prepare
    backend = ollama_with(
        ndjson_transport(
            [
                json.dumps({"message": {"content": "he"}, "done": False}),
                json.dumps({"message": {"content": "llo"}, "done": False}),
                json.dumps({"done": True, "done_reason": "stop", "eval_count": 2}),
            ]
        )
    )

    # act
    events = [event async for event in backend.generate([Message(role="user", content="hi")])]

    # verify
    assert events == [
        TextChunk(text="he"),
        TextChunk(text="llo"),
        Usage(prompt_tokens=0, completion_tokens=2, done_reason="stop"),
    ]


async def test_generate__skips_blank_lines_when_stream_contains_them(
    ollama_with: ModelFactory,
    ndjson_transport: TransportFactory,
) -> None:
    # prepare
    backend = ollama_with(
        ndjson_transport(["", json.dumps({"message": {"content": "hi"}, "done": False}), "  "])
    )

    # act
    events = [event async for event in backend.generate([])]

    # verify
    assert events == [TextChunk(text="hi")]


async def test_generate__raises_model_error_when_backend_returns_http_error(
    ollama_with: ModelFactory,
    ndjson_transport: TransportFactory,
) -> None:
    # prepare
    backend = ollama_with(ndjson_transport(['{"error":"model not found"}'], status_code=404))

    # act / verify
    with pytest.raises(ModelError, match="ollama returned 404"):
        [event async for event in backend.generate([])]


async def test_generate__raises_model_error_when_transport_fails(
    ollama_with: ModelFactory,
) -> None:
    # prepare
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    backend = ollama_with(httpx.MockTransport(explode))

    # act / verify
    with pytest.raises(ModelError, match="request failed"):
        [event async for event in backend.generate([])]


async def test_generate__posts_to_chat_endpoint_when_host_has_trailing_slash(
    ollama_with: ModelFactory,
) -> None:
    # prepare
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b'{"done":true}\n')

    backend = ollama_with(httpx.MockTransport(record), host="http://example.test:11434/")

    # act
    [event async for event in backend.generate([])]

    # verify
    assert str(seen[0].url) == "http://example.test:11434/api/chat"


# endregion

# region complete


async def test_complete__joins_text_and_thinking_when_draining_stream() -> None:
    # prepare
    backend = ScriptedModel(
        [
            ThinkingChunk(text="hm"),
            TextChunk(text="can"),
            ThinkingChunk(text="mm"),
            TextChunk(text="ned"),
            Usage(prompt_tokens=1, completion_tokens=2, done_reason="stop"),
        ]
    )

    # act
    completion = await backend.complete([Message(role="user", content="hi")])

    # verify
    assert completion.text == "canned"
    assert completion.thinking == "hmmm"
    assert completion.usage == Usage(prompt_tokens=1, completion_tokens=2, done_reason="stop")


async def test_complete__leaves_usage_none_when_stream_never_finishes() -> None:
    # prepare
    backend = ScriptedModel([TextChunk(text="partial")])

    # act
    completion = await backend.complete([])

    # verify
    assert completion.text == "partial"
    assert completion.usage is None


async def test_complete__returns_empty_completion_when_stream_is_empty() -> None:
    # prepare
    backend = ScriptedModel([])

    # act
    completion = await backend.complete([])

    # verify
    assert completion.text == ""
    assert completion.thinking == ""
    assert completion.usage is None


# endregion

# region client lifecycle


async def test_aclose__closes_client_when_model_created_it(model: OllamaModel) -> None:
    # prepare
    client = model._ensure_client()

    # act
    await model.aclose()

    # verify
    assert client.is_closed


async def test_aclose__leaves_client_open_when_caller_injected_it(
    ndjson_transport: TransportFactory,
) -> None:
    # prepare
    client = httpx.AsyncClient(transport=ndjson_transport([]))
    backend = OllamaModel("test-model", client=client)

    # act
    await backend.aclose()

    # verify
    assert not client.is_closed
    await client.aclose()


async def test_name__returns_the_backing_model_tag() -> None:
    # prepare
    backend = OllamaModel("qwen3:4b")

    # act
    name = backend.name

    # verify
    assert name == "qwen3:4b"


# endregion
