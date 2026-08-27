"""Unit tests for lila.tools — the one tool handler, over the fixture extension.

No network: the tests load ``tst/fixtures/lila-fixture``, which is a real extension
loaded by the real loader, so the handler is exercised end to end without a provider.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path as FilePath

import pytest
import yaml

from lila.executor import Graph, RunContext, RunError, parse_graph, run
from lila.extensions import load
from lila.resources import Instance, Registry
from lila.tools import default_handlers

# region fixtures

GraphFactory = Callable[[str], Graph]
FIXTURES = FilePath(__file__).parent / "fixtures"

FETCH_GRAPH = """
skill: fetching
resources: { inbox: test/fixture@1/mailbox }
entry: fetch
input:
  type: object
  properties:
    message_id: { type: string }
nodes:
  - id: fetch
    type: tool
    resource: inbox
    call: get_message
    args: { id: $.input.message_id }
edges:
  - { from: fetch, to: end }
return:
  subject: $.fetch.subject
"""

JOIN_GRAPH = """
skill: joining
resources: { text: test/fixture@1/text }
entry: joined
input:
  type: object
  properties:
    items: { type: array, items: { type: string } }
nodes:
  - id: joined
    type: tool
    resource: text
    call: join
    args: { items: $.input.items, sep: "-" }
edges:
  - { from: joined, to: end }
return:
  text: $.joined
"""


@pytest.fixture
def graph_from() -> GraphFactory:
    def build(source: str) -> Graph:
        return parse_graph(yaml.safe_load(source))

    return build


@pytest.fixture
def registry() -> Registry:
    """The fixture extension, loaded, with one instance of each of its resource types."""
    loaded = load(FIXTURES)
    mailbox = loaded.types["test/fixture@1/mailbox"]
    text = loaded.types["test/fixture@1/text"]
    loaded.register(
        Instance(
            name="fake-inbox",
            type="test/fixture@1/mailbox",
            handle=mailbox(host="mail.example.com", token="secret"),
        )
    )
    loaded.register(Instance(name="strings", type="test/fixture@1/text", handle=text()))
    return loaded


@pytest.fixture
def context(registry: Registry) -> RunContext:
    return RunContext(handlers=default_handlers(), registry=registry)


def bound(registry: Registry, name: str, instance: str) -> dict[str, Instance]:
    """One resource name bound to one configured instance."""
    return {name: registry.instance(instance)}


# endregion

# region tool_handler


async def test_tool_handler__calls_the_tool_with_rendered_args(
    graph_from: GraphFactory, context: RunContext, registry: Registry
) -> None:
    # prepare
    graph = graph_from(FETCH_GRAPH)

    # act
    result = await run(graph, {"message_id": "7"}, context, bound(registry, "inbox", "fake-inbox"))

    # verify
    assert result.output == {"subject": "subject 7"}


async def test_tool_handler__records_the_resource_name_not_the_handle(
    graph_from: GraphFactory, context: RunContext, registry: Registry
) -> None:
    # prepare
    graph = graph_from(FETCH_GRAPH)

    # act
    result = await run(graph, {"message_id": "7"}, context, bound(registry, "inbox", "fake-inbox"))

    # verify
    assert result.record.nodes[0].resources == ("inbox",)
    assert result.record.nodes[0].inputs == {"id": "7"}


async def test_tool_handler__validates_the_result_against_the_tools_own_schema(
    graph_from: GraphFactory, context: RunContext, registry: Registry
) -> None:
    # prepare — the graph declares no out:; the tool's return annotation is the schema
    graph = graph_from(FETCH_GRAPH)

    # act
    result = await run(graph, {"message_id": "7"}, context, bound(registry, "inbox", "fake-inbox"))

    # verify
    assert result.memory.history("fetch") == [{"id": "7", "subject": "subject 7"}]


async def test_tool_handler__raises_run_error_naming_the_node_when_the_tool_fails(
    graph_from: GraphFactory, context: RunContext, registry: Registry
) -> None:
    # prepare
    graph = graph_from(FETCH_GRAPH)

    # act / verify
    with pytest.raises(RunError, match="not found") as caught:
        await run(graph, {"message_id": "missing"}, context, bound(registry, "inbox", "fake-inbox"))
    assert caught.value.node_id == "fetch"


async def test_tool_handler__raises_run_error_when_the_type_has_no_such_tool(
    graph_from: GraphFactory, context: RunContext, registry: Registry
) -> None:
    # prepare
    graph = graph_from(FETCH_GRAPH.replace("call: get_message", "call: burn_inbox"))

    # act / verify
    with pytest.raises(RunError, match="no tool 'burn_inbox'") as caught:
        await run(graph, {"message_id": "7"}, context, bound(registry, "inbox", "fake-inbox"))
    assert caught.value.node_id == "fetch"


async def test_tool_handler__raises_run_error_when_args_do_not_fit_the_tool_schema(
    graph_from: GraphFactory, context: RunContext, registry: Registry
) -> None:
    # prepare — list_messages takes an integer limit
    graph = graph_from(
        FETCH_GRAPH.replace("call: get_message", "call: list_messages").replace(
            "args: { id: $.input.message_id }", "args: { limit: $.input.message_id }"
        )
    )

    # act / verify
    with pytest.raises(RunError, match="args failed list_messages schema"):
        await run(graph, {"message_id": "7"}, context, bound(registry, "inbox", "fake-inbox"))


async def test_tool_handler__raises_run_error_when_the_resource_is_not_bound(
    graph_from: GraphFactory, context: RunContext
) -> None:
    # prepare
    graph = graph_from(FETCH_GRAPH)

    # act / verify
    with pytest.raises(RunError, match="unbound"):
        await run(graph, {"message_id": "7"}, context, {})


async def test_tool_handler__runs_a_tool_on_a_stateless_resource(
    graph_from: GraphFactory, context: RunContext, registry: Registry
) -> None:
    # prepare — a resource with no fields is how a transform reaches a graph
    graph = graph_from(JOIN_GRAPH)

    # act
    result = await run(
        graph, {"items": ["a", "b", "c"]}, context, bound(registry, "text", "strings")
    )

    # verify
    assert result.output == {"text": "a-b-c"}


async def test_tool_handler__raises_run_error_when_no_registry_is_bound(
    graph_from: GraphFactory, registry: Registry
) -> None:
    # prepare
    graph = graph_from(FETCH_GRAPH)

    # act / verify
    with pytest.raises(RunError, match="no registry bound"):
        await run(
            graph,
            {"message_id": "7"},
            RunContext(handlers=default_handlers()),
            bound(registry, "inbox", "fake-inbox"),
        )


# endregion

# region default_handlers


def test_default_handlers__registers_one_handler_per_node_type() -> None:
    # act
    handlers = default_handlers()

    # verify — transports are not node types
    assert sorted(handlers) == ["llm", "skill.run", "tool"]


# endregion
