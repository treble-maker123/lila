"""``test/email@1/imap`` — a mailbox over IMAP with an app password.

IMAP flattens Gmail's model: labels are folders, so a multi-label message does not
round-trip and Gmail search syntax is out of reach. The skills here are written to that
grain, one folder per message.
"""

from __future__ import annotations

import email
import imaplib
import re
from contextlib import contextmanager
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
from html.parser import HTMLParser
from typing import Iterator, TypedDict

from lila.ext import Secret, ToolError, resource, tool

IMAP_PORT = 993

# ~10k tokens: comfortably inside a 32k window, so one message cannot crowd out the rest
# of a prompt. A graph can lower it per call with ``max_body``.
MAX_BODY = 40_000
_SKIPPED_TAGS = frozenset({"script", "style", "head", "title"})
# Marketers pad the inbox preview with invisible characters. They carry no meaning and
# survive tag stripping, so a "1,600 character" body can be mostly spacer.
_INVISIBLE = str.maketrans(
    {" ": " ", " ": " ", " ": " ", "­": "", "​": "", "‌": "", "‍": "", "⁠": "", "﻿": "", "͏": ""}
)
_BREAKING_TAGS = frozenset({"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"})


@resource
@dataclass(frozen=True)
class Imap:
    """Connection settings for one mailbox. These fields are the config.toml shape."""

    host: str
    username: str
    password: Secret
    port: int = IMAP_PORT
    folder: str = "INBOX"

    @contextmanager
    def session(self, folder: str | None = None, readonly: bool = True) -> Iterator[imaplib.IMAP4_SSL]:
        """Open a TLS session, log in, select a folder, and close on exit.

        Raises:
            ToolError: the connection, the login, or the folder select failed.
        """
        try:
            client = imaplib.IMAP4_SSL(self.host, self.port)
            client.login(self.username, self.password)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ToolError(f"IMAP login to {self.host} failed: {exc}") from exc
        try:
            selected = folder or self.folder
            status, _ = client.select(f'"{selected}"', readonly=readonly)
            if status != "OK":
                raise ToolError(f"cannot select folder {selected!r}")
            yield client
        finally:
            client.logout()


class Listing(TypedDict):
    """What ``list_messages`` returns."""

    ids: list[str]


class Message(TypedDict):
    """What ``get_message`` returns."""

    id: str
    sender: str
    subject: str
    body: str


class Moved(TypedDict):
    """What ``move_message`` returns."""

    id: str
    folder: str


@tool
def list_messages(inbox: Imap, folder: str = "", unread: bool = False, limit: int = 0) -> Listing:
    """Message ids in a folder, all of them or only the unread ones.

    ``limit`` keeps the last N — IMAP returns oldest first, so that is the most recent N;
    0 means no limit. Ids are sequence numbers, so they renumber after an expunge.

    Raises:
        ToolError: login, folder select, or search failed.
    """
    with inbox.session(folder or None, readonly=True) as client:
        status, data = client.search(None, "UNSEEN" if unread else "ALL")
        if status != "OK":
            raise ToolError(f"search in {folder or inbox.folder!r} failed")
        ids = [uid.decode() for uid in (data[0] or b"").split()]
    return {"ids": ids[-limit:] if limit > 0 else ids}


@tool
def get_message(inbox: Imap, id: str, max_body: int = MAX_BODY) -> Message:
    """Fetch one message as id, sender, subject, and plain-text body.

    ``BODY.PEEK[]`` rather than ``RFC822``: a plain fetch sets ``\\Seen``, which would
    make reading an unread message mark it read.

    Raises:
        ToolError: login or folder select failed, or the message is not there.
    """
    with inbox.session(readonly=True) as client:
        status, data = client.fetch(id, "(BODY.PEEK[])")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            raise ToolError(f"message {id!r} not found")
        message = email.message_from_bytes(data[0][1])
    return {
        "id": id,
        "sender": _header(message, "From"),
        "subject": _header(message, "Subject"),
        "body": _body(message, max_body),
    }


@tool
def move_message(inbox: Imap, id: str, folder: str) -> Moved:
    """Copy a message to another folder and expunge the original.

    Raises:
        ToolError: login, folder select, or the copy failed.
    """
    with inbox.session(readonly=False) as client:
        status, _ = client.copy(id, f'"{folder}"')
        if status != "OK":
            raise ToolError(f"cannot copy {id!r} to {folder!r}")
        client.store(id, "+FLAGS", "\\Deleted")
        client.expunge()
    return {"id": id, "folder": folder}


def _header(message: EmailMessage, name: str) -> str:
    """One decoded header, or an empty string when absent."""
    raw = message.get(name)
    return str(make_header(decode_header(raw))) if raw else ""


def _body(message: EmailMessage, limit: int = MAX_BODY) -> str:
    """The message as plain text: ``text/plain`` when there is one, else HTML stripped.

    Marketing mail is often HTML-only, and raw markup is mostly markup — 35 KB of tags
    around a paragraph of text, which buries the content and blows a context window.

    Truncated to ``limit`` characters so one message cannot eat the whole prompt.
    """
    text = ""
    if message.is_multipart():
        parts = list(message.walk())
        text = next((_decode(p) for p in parts if p.get_content_type() == "text/plain"), "")
        if not text:
            html = next((_decode(p) for p in parts if p.get_content_type() == "text/html"), "")
            text = _from_html(html)
    elif message.get_content_type() == "text/html":
        text = _from_html(_decode(message))
    else:
        text = _decode(message)
    return text[:limit]


def _decode(part: EmailMessage) -> str:
    """Decode one part's payload to text, replacing undecodable bytes."""
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return str(part.get_payload())


class _TextExtractor(HTMLParser):
    """Text out of HTML: drops script and style, keeps image alt text.

    Images are lost, which is the right trade for a summary — but alt text is often the
    only words in a marketing e-mail, so it is kept.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag == "img":
            alt = next((value for name, value in attrs if name == "alt" and value), "")
            if alt:
                self.chunks.append(alt)
        elif tag in _BREAKING_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skipping:
            self._skipping -= 1
        elif tag in _BREAKING_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping and data.strip():
            self.chunks.append(data.strip())


def _from_html(html: str) -> str:
    """HTML to readable text, with runs of blank space collapsed."""
    if not html.strip():
        return ""
    extractor = _TextExtractor()
    extractor.feed(html)
    joined = " ".join(extractor.chunks).translate(_INVISIBLE)
    # Collapse the whitespace the markup left behind, keeping paragraph breaks.
    collapsed = re.sub(r"[ \t]*\n[ \t]*(\n[ \t]*)+", "\n\n", re.sub(r"[ \t]+", " ", joined))
    return collapsed.strip()
