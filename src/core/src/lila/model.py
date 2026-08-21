"""Model protocol and backends.

The protocol is the boundary between the graph executor and any inference backend:
a node streams typed events and never touches a provider SDK.
"""

from __future__ import annotations

import json
from abc import abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Protocol

import httpx

Role = Literal["system", "user", "assistant"]

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
# The first token can lag while ollama loads weights, so the read budget is generous.
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class GenerateOptions:
    # Raw JSON Schema. When set, the backend constrains decoding to it — this is how an
    # LLM node declares its output shape instead of asking for JSON in the prompt.
    json_schema: dict[str, Any] | None = None
    # Deterministic by default; a node opts into sampling explicitly.
    temperature: float = 0.0
    seed: int | None = None
    max_tokens: int | None = None
    stop: tuple[str, ...] = ()
    # Model context window. None leaves the backend default.
    context_length: int | None = None
    # Reasoning models only. None leaves the backend default.
    think: bool | None = None


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True, slots=True)
class ThinkingChunk:
    text: str
    kind: Literal["thinking"] = "thinking"


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    # Why the backend stopped, e.g. "stop" or "length".
    done_reason: str | None = None
    kind: Literal["usage"] = "usage"


# Discriminated on ``kind`` so renderers and the executor can match without isinstance chains.
GenerateEvent = TextChunk | ThinkingChunk | Usage


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    thinking: str
    usage: Usage | None = None


class ModelError(RuntimeError):
    """A backend failed to produce a completion."""


class Model(Protocol):
    """Inference backend.

    Backends subclass this to inherit ``complete``; a class matching the shape also
    conforms structurally, but then owns both methods itself.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier of the underlying model, e.g. ``qwen3.5:9b``."""

    @abstractmethod
    def generate(
        self,
        messages: list[Message],
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[GenerateEvent]:
        """Stream events for ``messages``. Raises ModelError when the backend fails."""
        ...

    async def complete(
        self,
        messages: list[Message],
        options: GenerateOptions | None = None,
    ) -> Completion:
        """Drain ``generate`` into one result, for callers that do not render tokens."""
        text: list[str] = []
        thinking: list[str] = []
        usage: Usage | None = None
        async for event in self.generate(messages, options):
            match event:
                case TextChunk():
                    text.append(event.text)
                case ThinkingChunk():
                    thinking.append(event.text)
                case Usage():
                    usage = event
        return Completion(text="".join(text), thinking="".join(thinking), usage=usage)


class OllamaModel(Model):
    """Ollama backend, streaming NDJSON from /api/chat.

    Weights live in the ollama daemon, so restarting LILA does not reload the model.
    """

    def __init__(
        self,
        model: str,
        *,
        host: str = DEFAULT_OLLAMA_HOST,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._model = model
        self._host = host.rstrip("/")
        self._timeout = timeout
        # An injected client is the caller's to close.
        self._client = client
        self._owns_client = client is None

    @property
    def name(self) -> str:
        return self._model

    async def __aenter__(self) -> OllamaModel:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _request_body(
        self,
        messages: list[Message],
        options: GenerateOptions | None,
    ) -> dict[str, Any]:
        opts = options or GenerateOptions()
        # Ollama nests sampling knobs under "options"; unset ones are omitted so its own
        # defaults win rather than being overwritten with ours.
        sampling: dict[str, Any] = {"temperature": opts.temperature}
        if opts.seed is not None:
            sampling["seed"] = opts.seed
        if opts.max_tokens is not None:
            sampling["num_predict"] = opts.max_tokens
        if opts.stop:
            sampling["stop"] = list(opts.stop)
        if opts.context_length is not None:
            sampling["num_ctx"] = opts.context_length

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": sampling,
        }
        if opts.json_schema is not None:
            body["format"] = opts.json_schema
        if opts.think is not None:
            body["think"] = opts.think
        return body

    @staticmethod
    def _parse_chat_line(line: str) -> list[GenerateEvent]:
        """Turn one NDJSON line from /api/chat into zero or more events."""
        try:
            payload: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ModelError(f"ollama sent a malformed line: {line!r}") from exc

        if error := payload.get("error"):
            raise ModelError(f"ollama reported an error: {error}")

        events: list[GenerateEvent] = []
        message: dict[str, Any] = payload.get("message") or {}
        if thinking := message.get("thinking"):
            events.append(ThinkingChunk(text=thinking))
        if content := message.get("content"):
            events.append(TextChunk(text=content))
        if payload.get("done"):
            events.append(
                Usage(
                    prompt_tokens=payload.get("prompt_eval_count", 0),
                    completion_tokens=payload.get("eval_count", 0),
                    done_reason=payload.get("done_reason"),
                )
            )
        return events

    async def generate(
        self,
        messages: list[Message],
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[GenerateEvent]:
        client = self._ensure_client()
        body = self._request_body(messages, options)
        try:
            async with client.stream("POST", f"{self._host}/api/chat", json=body) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode(errors="replace").strip()
                    raise ModelError(f"ollama returned {response.status_code}: {detail}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    for event in self._parse_chat_line(line):
                        yield event
        except httpx.HTTPError as exc:
            raise ModelError(f"ollama request failed: {exc}") from exc
