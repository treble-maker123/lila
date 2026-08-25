"""Unit tests for lila.tools. No network — the IMAP client is faked at its boundary."""

from __future__ import annotations

import email.message
from collections.abc import Callable

import pytest
import yaml

from lila.executor import Graph, RunContext, RunError, parse_graph, run
from lila.resources import (
    ArgName,
    BindingName,
    CallName,
    InterfaceName,
    ResourceError,
    ResourceRegistry,
)
from lila.tools import ImapMailbox, default_handlers
from lila.values import Json

# region fixtures

GraphFactory = Callable[..., Graph]


class FakeImap:
    """Stands in for imaplib.IMAP4_SSL, recording the commands it was given."""

    def __init__(self, message: bytes = b"") -> None:
        self.message = message
        self.commands: list[tuple[str, ...]] = []
        self.logged_out = False

    def login(self, username: str, password: str) -> None:
        self.commands.append(("login", username, password))

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.commands.append(("select", folder, str(readonly)))
        return "OK", [b"1"]

    def search(self, charset: str | None, criteria: str) -> tuple[str, list[bytes]]:
        self.commands.append(("search", criteria))
        return "OK", [b"1 2 3"]

    def fetch(self, message_id: str, parts: str) -> tuple[str, list[tuple[bytes, bytes]]]:
        self.commands.append(("fetch", message_id, parts))
        return "OK", [(b"1 (RFC822 {1})", self.message)]

    def copy(self, message_id: str, folder: str) -> tuple[str, list[bytes]]:
        self.commands.append(("copy", message_id, folder))
        return "OK", [b""]

    def store(self, message_id: str, command: str, flags: str) -> tuple[str, list[bytes]]:
        self.commands.append(("store", message_id, command, flags))
        return "OK", [b""]

    def expunge(self) -> tuple[str, list[bytes]]:
        self.commands.append(("expunge",))
        return "OK", [b""]

    def logout(self) -> tuple[str, list[bytes]]:
        self.logged_out = True
        return "BYE", [b""]


class FakeMailbox:
    """A mailbox@1 answering from a dict."""

    def __init__(self, messages: dict[str, dict[str, Json]]) -> None:
        self.messages = messages
        self.calls: list[tuple[CallName, dict[ArgName, Json]]] = []

    @property
    def name(self) -> BindingName:
        return "fake-inbox"

    @property
    def interface(self) -> InterfaceName:
        return "mailbox@1"

    def call(self, operation: CallName, args: dict[ArgName, Json]) -> dict[str, Json]:
        self.calls.append((operation, args))
        if operation != "get_message":
            raise ResourceError(f"no operation {operation!r}")
        return self.messages[str(args["id"])]


@pytest.fixture
def graph_from() -> GraphFactory:
    def build(source: str) -> Graph:
        return parse_graph(yaml.safe_load(source))

    return build


@pytest.fixture
def mailbox_with(monkeypatch: pytest.MonkeyPatch) -> Callable[..., tuple[ImapMailbox, FakeImap]]:
    """Build a mailbox whose IMAP connection is a FakeImap."""

    def build(message: bytes = b"") -> tuple[ImapMailbox, FakeImap]:
        fake = FakeImap(message)
        mailbox = ImapMailbox(
            "test-inbox", host="imap.example.com", username="me", password="secret"
        )
        monkeypatch.setattr(mailbox, "_connect", lambda: fake)
        return mailbox, fake

    return build


def plain_message(sender: str, subject: str, body: str) -> bytes:
    message = email.message.EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(body)
    return message.as_bytes()


FETCH_GRAPH = """
skill: fetching
requires: { inbox: mailbox@1 }
entry: fetch
input:
  type: object
  properties:
    message_id: { type: string }
nodes:
  - id: fetch
    type: tool.api
    uses: inbox
    call: get_message
    args: { id: $.input.message_id }
    out:
      type: object
      properties:
        subject: { type: string }
      required: [subject]
edges:
  - { from: fetch, to: end }
return:
  subject: $.fetch.subject
"""

# endregion

# region api_handler


async def test_api_handler__calls_the_bound_slot_with_rendered_args(
    graph_from: GraphFactory,
) -> None:
    # prepare
    mailbox = FakeMailbox({"7": {"subject": "hello"}})
    context = RunContext(handlers=default_handlers())

    # act
    result = await run(graph_from(FETCH_GRAPH), {"message_id": "7"}, context, {"inbox": mailbox})

    # verify
    assert mailbox.calls == [("get_message", {"id": "7"})]
    assert result.output == {"subject": "hello"}


async def test_api_handler__records_the_slot_name_not_the_handle(
    graph_from: GraphFactory,
) -> None:
    # prepare
    mailbox = FakeMailbox({"7": {"subject": "hello"}})
    context = RunContext(handlers=default_handlers())

    # act
    result = await run(graph_from(FETCH_GRAPH), {"message_id": "7"}, context, {"inbox": mailbox})

    # verify
    assert result.record.nodes[0].resources == ("inbox",)
    assert result.record.nodes[0].inputs == {"id": "7"}


async def test_api_handler__raises_run_error_naming_the_node_when_the_call_fails(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from(FETCH_GRAPH.replace("call: get_message", "call: burn_inbox"))
    context = RunContext(handlers=default_handlers())

    # act / verify
    with pytest.raises(RunError) as caught:
        await run(graph, {"message_id": "7"}, context, {"inbox": FakeMailbox({})})
    assert caught.value.node_id == "fetch"


# endregion

# region local_handler


async def test_local_handler__calls_the_registered_callable_with_rendered_args(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        skill: transforming
        entry: shout
        input: { type: object, properties: { text: { type: string } } }
        nodes:
          - id: shout
            type: tool.local
            call: upper
            args: { text: $.input.text }
            out: { type: object, properties: { text: { type: string } } }
        edges:
          - { from: shout, to: end }
        return: { text: $.shout.text }
        """)
    context = RunContext(
        handlers=default_handlers(),
        locals={"upper": lambda args: {"text": str(args["text"]).upper()}},
    )

    # act
    result = await run(graph, {"text": "hi"}, context)

    # verify
    assert result.output == {"text": "HI"}


async def test_local_handler__raises_run_error_when_the_callable_is_not_registered(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        skill: transforming
        entry: shout
        nodes:
          - { id: shout, type: tool.local, call: upper }
        edges:
          - { from: shout, to: end }
        """)

    # act / verify
    with pytest.raises(RunError, match="no local callable"):
        await run(graph, {}, RunContext(handlers=default_handlers()))


# endregion

# region ImapMailbox

MailboxFactory = Callable[..., tuple[ImapMailbox, FakeImap]]


def test_get_message__returns_headers_and_plain_body(mailbox_with: MailboxFactory) -> None:
    # prepare
    mailbox, _ = mailbox_with(plain_message("a@example.com", "one", "body text"))

    # act
    message = mailbox.call("get_message", {"id": "1"})

    # verify
    assert message == {
        "id": "1",
        "from": "a@example.com",
        "subject": "one",
        "body": "body text\n",
    }


def test_get_message__selects_the_folder_read_only(mailbox_with: MailboxFactory) -> None:
    # prepare
    mailbox, fake = mailbox_with(plain_message("a@example.com", "one", "b"))

    # act
    mailbox.call("get_message", {"id": "1"})

    # verify
    assert ("select", '"INBOX"', "True") in fake.commands
    assert fake.logged_out is True


def test_list_messages__returns_the_ids_in_the_folder(mailbox_with: MailboxFactory) -> None:
    # prepare
    mailbox, _ = mailbox_with()

    # act
    listing = mailbox.call("list_messages", {"folder": "INBOX"})

    # verify
    assert listing == {"ids": ["1", "2", "3"]}


def test_move_message__copies_then_deletes_the_original(mailbox_with: MailboxFactory) -> None:
    # prepare
    mailbox, fake = mailbox_with()

    # act
    moved = mailbox.call("move_message", {"id": "1", "folder": "action"})

    # verify
    assert moved == {"id": "1", "folder": "action"}
    assert [command[0] for command in fake.commands] == [
        "select",
        "copy",
        "store",
        "expunge",
    ]


def test_call__raises_resource_error_when_the_operation_is_unknown(
    mailbox_with: MailboxFactory,
) -> None:
    # prepare
    mailbox, _ = mailbox_with()

    # act / verify
    with pytest.raises(ResourceError, match="no operation"):
        mailbox.call("delete_everything", {})


def test_interface__is_mailbox_v1(mailbox_with: MailboxFactory) -> None:
    # prepare
    mailbox, _ = mailbox_with()

    # act / verify
    assert mailbox.interface == "mailbox@1"


# endregion

# region ResourceRegistry


def test_bind__maps_slots_to_instances_when_interfaces_match() -> None:
    # prepare
    mailbox = FakeMailbox({})
    registry = ResourceRegistry()
    registry.register(mailbox)

    # act
    bound = registry.bind({"inbox": "mailbox@1"}, {"inbox": "fake-inbox"})

    # verify
    assert bound == {"inbox": mailbox}


def test_bind__raises_resource_error_when_the_interface_differs() -> None:
    # prepare
    registry = ResourceRegistry({"fake-inbox": FakeMailbox({})})

    # act / verify
    with pytest.raises(ResourceError, match="calendar@1"):
        registry.bind({"inbox": "calendar@1"}, {"inbox": "fake-inbox"})


def test_bind__raises_resource_error_when_a_slot_is_unbound() -> None:
    # prepare
    registry = ResourceRegistry()

    # act / verify
    with pytest.raises(ResourceError, match="unbound"):
        registry.bind({"inbox": "mailbox@1"}, {})


# endregion
