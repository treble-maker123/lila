"""``tool.*`` handlers and the first ``mailbox@1`` implementation.

All transports share one contract — JSON args in, JSON result out, one validation and
record path — so adding a transport is an adapter, not a branch in the executor.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
from email.header import decode_header, make_header
from email.message import Message as EmailMessage

from lila.executor import (
    ApiConfig,
    Handler,
    LocalConfig,
    NodeCall,
    NodeResult,
    NodeType,
    RunError,
    llm_handler,
    skill_run_handler,
)
from lila.resources import ArgName, BindingName, CallName, InterfaceName, ResourceError
from lila.values import Json

IMAP_PORT = 993
MAILBOX_INTERFACE: InterfaceName = "mailbox@1"


async def api_handler(call: NodeCall) -> NodeResult:
    """Resolve ``uses:`` to a handle, render args, call, hand the result to the loop.

    Raises:
        RunError: the slot is unbound, an args path does not resolve, or the resource
            call fails.
    """
    node = call.node
    config = node.config
    assert isinstance(config, ApiConfig)
    handle = call.memory.resources.get(config.uses)
    if handle is None:
        raise RunError(f"slot {config.uses!r} is not bound", node_id=node.id)
    args = call.memory.resolve_mapping(config.args)
    try:
        # The transport is blocking, so it runs off the event loop.
        output = await asyncio.to_thread(handle.call, config.call, args)
    except ResourceError as exc:
        raise RunError(str(exc), node_id=node.id) from exc
    return NodeResult(output=output, inputs=args, resources=(config.uses,))


async def local_handler(call: NodeCall) -> NodeResult:
    """In-process callable — the transform escape hatch.

    Raises:
        RunError: nothing is registered under ``call:``, or an args path does not
            resolve. Whatever the callable raises propagates as-is.
    """
    node = call.node
    config = node.config
    assert isinstance(config, LocalConfig)
    callable_ = call.context.locals.get(config.call)
    if callable_ is None:
        raise RunError(f"no local callable registered as {config.call!r}", node_id=node.id)
    args = call.memory.resolve_mapping(config.args)
    return NodeResult(output=callable_(args), inputs=args)


def default_handlers() -> dict[NodeType, Handler]:
    """Node type -> handler. T5/T6 register here rather than editing the run loop."""
    return {
        "llm": llm_handler,
        "tool.api": api_handler,
        "tool.local": local_handler,
        "skill.run": skill_run_handler,
    }


class ImapMailbox:
    """``mailbox@1`` over IMAP with an app password.

    IMAP flattens Gmail's model — labels are folders, so a multi-label message does not
    round-trip and Gmail search syntax is out of reach. The e-mail skill is written to
    that grain: one folder per message.
    """

    def __init__(
        self,
        name: BindingName,
        *,
        host: str,
        username: str,
        password: str,
        port: int = IMAP_PORT,
        folder: str = "INBOX",
    ) -> None:
        """Hold the connection settings; no connection is opened until a call."""
        self._name = name
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._folder = folder

    @property
    def name(self) -> BindingName:
        """Binding name of this instance."""
        return self._name

    @property
    def interface(self) -> InterfaceName:
        """The interface this resource implements — always ``mailbox@1``."""
        return MAILBOX_INTERFACE

    def call(self, operation: CallName, args: dict[ArgName, Json]) -> dict[str, Json]:
        """Dispatch one ``mailbox@1`` operation to its method.

        Raises:
            ResourceError: unknown operation, or the IMAP call failed.
            KeyError: a required arg is missing.
        """
        match operation:
            case "list_messages":
                return self.list_messages(str(args.get("folder", self._folder)))
            case "get_message":
                return self.get_message(str(args["id"]))
            case "move_message":
                return self.move_message(str(args["id"]), str(args["folder"]))
            case _:
                raise ResourceError(f"{MAILBOX_INTERFACE} has no operation {operation!r}")

    def _connect(self) -> imaplib.IMAP4_SSL:
        """Open a TLS connection and log in.

        Raises:
            ResourceError: the connection or login failed.
        """
        try:
            client = imaplib.IMAP4_SSL(self._host, self._port)
            client.login(self._username, self._password)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ResourceError(f"IMAP login to {self._host} failed: {exc}") from exc
        return client

    def _select(self, client: imaplib.IMAP4_SSL, folder: str, readonly: bool) -> None:
        """Select a folder on an open connection.

        Raises:
            ResourceError: the folder cannot be selected.
        """
        status, _ = client.select(f'"{folder}"', readonly=readonly)
        if status != "OK":
            raise ResourceError(f"cannot select folder {folder!r}")

    def list_messages(self, folder: str) -> dict[str, Json]:
        """Every message id in a folder.

        Raises:
            ResourceError: login, folder select, or search failed.
        """
        client = self._connect()
        try:
            self._select(client, folder, readonly=True)
            status, data = client.search(None, "ALL")
            if status != "OK":
                raise ResourceError(f"search in {folder!r} failed")
            ids = [uid.decode() for uid in (data[0] or b"").split()]
        finally:
            client.logout()
        return {"ids": ids}

    def get_message(self, message_id: str) -> dict[str, Json]:
        """Fetch one message as id, from, subject, and plain-text body.

        Raises:
            ResourceError: login or folder select failed, or the message is not there.
        """
        client = self._connect()
        try:
            self._select(client, self._folder, readonly=True)
            status, data = client.fetch(message_id, "(RFC822)")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                raise ResourceError(f"message {message_id!r} not found")
            message = email.message_from_bytes(data[0][1])
        finally:
            client.logout()
        return {
            "id": message_id,
            "from": _header(message, "From"),
            "subject": _header(message, "Subject"),
            "body": _body(message),
        }

    def move_message(self, message_id: str, folder: str) -> dict[str, Json]:
        """Copy a message to another folder and expunge the original.

        Raises:
            ResourceError: login, folder select, or the copy failed.
        """
        client = self._connect()
        try:
            self._select(client, self._folder, readonly=False)
            status, _ = client.copy(message_id, f'"{folder}"')
            if status != "OK":
                raise ResourceError(f"cannot copy {message_id!r} to {folder!r}")
            client.store(message_id, "+FLAGS", "\\Deleted")
            client.expunge()
        finally:
            client.logout()
        return {"id": message_id, "folder": folder}


def _header(message: EmailMessage, name: str) -> str:
    """One decoded header, or an empty string when absent."""
    raw = message.get(name)
    return str(make_header(decode_header(raw))) if raw else ""


def _body(message: EmailMessage) -> str:
    """First text/plain part, falling back to the payload as-is."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                return _decode(part)
        return ""
    return _decode(message)


def _decode(part: EmailMessage) -> str:
    """Decode one part's payload to text, replacing undecodable bytes."""
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return str(part.get_payload())
