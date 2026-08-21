"""Integration test for the public Model API against a live ollama.

Skips when no daemon is reachable. Override the target with LILA_OLLAMA_HOST and
LILA_OLLAMA_MODEL; inside Docker the host is typically host.docker.internal.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import httpx
import pytest

from lila.model import (
    DEFAULT_OLLAMA_HOST,
    GenerateOptions,
    Message,
    OllamaModel,
    TextChunk,
    Usage,
)

HOST = os.environ.get("LILA_OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
MODEL = os.environ.get("LILA_OLLAMA_MODEL", "qwen3:4b")

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {"route": {"type": "string", "enum": ["reply", "no_action"]}},
    "required": ["route"],
}


def require_ollama() -> None:
    """Skip rather than fail when the daemon or the model is unavailable."""
    try:
        response = httpx.get(f"{HOST.rstrip('/')}/api/tags", timeout=2.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"no ollama at {HOST}: {exc}")

    tags = {model["name"] for model in response.json().get("models", [])}
    if MODEL not in tags:
        pytest.skip(f"model {MODEL} not pulled on {HOST}")


@pytest.fixture
async def model() -> AsyncIterator[OllamaModel]:
    require_ollama()
    async with OllamaModel(MODEL, host=HOST) as backend:
        yield backend


async def test_generate__streams_text_and_usage_from_live_ollama(model: OllamaModel) -> None:
    # prepare
    messages = [Message(role="user", content="Reply with the single word: ready")]
    options = GenerateOptions(think=False, max_tokens=32)

    # act
    events = [event async for event in model.generate(messages, options)]

    # verify
    assert any(isinstance(event, TextChunk) for event in events)
    assert isinstance(events[-1], Usage)
    assert events[-1].prompt_tokens > 0


async def test_complete__returns_schema_valid_json_from_live_ollama(model: OllamaModel) -> None:
    # prepare
    messages = [Message(role="user", content="Email: 'Your package shipped.' Route it.")]
    options = GenerateOptions(json_schema=ROUTE_SCHEMA, think=False, max_tokens=64)

    # act
    completion = await model.complete(messages, options)

    # verify
    assert json.loads(completion.text)["route"] in {"reply", "no_action"}
    assert completion.usage is not None
