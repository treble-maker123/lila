"""``test/fixture@1`` — a mailbox that answers from memory, and a stateless toolkit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from lila.ext import Secret, ToolError, resource, tool


@resource
@dataclass(frozen=True)
class Mailbox:
    """A mailbox with no network behind it. Its fields are the config.toml shape."""

    host: str
    token: Secret
    port: int = 993


@resource
@dataclass(frozen=True)
class Text:
    """No fields — a stateless extension, which is what a transform is."""


class Message(TypedDict):
    """What ``get_message`` returns."""

    id: str
    subject: str


class Listing(TypedDict):
    """What ``list_messages`` returns."""

    ids: list[str]


@tool
def get_message(inbox: Mailbox, id: str) -> Message:
    """One message, made up from its id."""
    if id == "missing":
        raise ToolError(f"message {id!r} not found")
    return {"id": id, "subject": f"subject {id}"}


@tool
def list_messages(inbox: Mailbox, limit: int = 0) -> Listing:
    """Three ids, or the last ``limit`` of them."""
    ids = ["1", "2", "3"]
    return {"ids": ids[-limit:] if limit > 0 else ids}


@tool
def join(text: Text, items: list[str], sep: str = ", ") -> str:
    """Join a list into one string — the transform escape hatch."""
    return sep.join(items)


@tool
def shout(phrase: str) -> str:
    """Uppercase it — a pure tool: no resource, so nothing to bind or stub."""
    return phrase.upper()
