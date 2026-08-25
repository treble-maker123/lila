"""Unit tests for lila.verification. Pure, no I/O."""

from __future__ import annotations

from collections.abc import Callable

import pytest
import yaml

from lila.executor import Graph, parse_graph
from lila.verification import Issue, check

# region fixtures

GraphFactory = Callable[..., Graph]


@pytest.fixture
def graph_from() -> GraphFactory:
    """Build a Graph from YAML source text."""

    def build(source: str) -> Graph:
        return parse_graph(yaml.safe_load(source))

    return build


def rules(issues: list[Issue]) -> list[str]:
    return [issue.rule for issue in issues]


VALID_GRAPH = """
skill: valid
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
  - id: classify
    type: llm
    prompt: "{{ $.fetch.subject }}"
    out:
      type: object
      properties:
        route: { type: string }
edges:
  - { from: fetch, to: classify }
  - { from: classify, to: end, when: $.classify.route == "reply" }
  - { from: classify, to: end, when: true }
return:
  route: $.classify.route
"""

# endregion

# region check


def test_check__returns_no_issues_when_the_graph_is_runnable(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from(VALID_GRAPH)

    # act
    issues = check(graph, bindings={"inbox": "gmail-personal"})

    # verify
    assert issues == []


def test_check__reports_duplicate_node_ids(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: dupes
        entry: a
        nodes:
          - { id: a, type: tool.local, call: x }
          - { id: a, type: tool.local, call: y }
        edges:
          - { from: a, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert "unique-node-id" in rules(issues)


def test_check__reports_a_slot_that_collides_with_a_node_id(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: collision
        requires: { fetch: mailbox@1 }
        entry: fetch
        nodes:
          - { id: fetch, type: tool.local, call: x }
        edges:
          - { from: fetch, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert "slot-node-collision" in rules(issues)


def test_check__reports_an_edge_to_an_unknown_node(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: dangling
        entry: a
        nodes:
          - { id: a, type: tool.local, call: x }
        edges:
          - { from: a, to: nowhere }
        """)

    # act
    issues = check(graph)

    # verify
    assert "edge-target" in rules(issues)


def test_check__reports_edges_listed_after_an_unguarded_one(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: dead-edges
        entry: a
        nodes:
          - { id: a, type: tool.local, call: x, out: { type: object } }
          - { id: b, type: tool.local, call: y }
        edges:
          - { from: a, to: end, when: true }
          - { from: a, to: b }
          - { from: b, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert "unreachable-edge" in rules(issues)


def test_check__reports_a_node_unreachable_from_entry(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: orphan
        entry: a
        nodes:
          - { id: a, type: tool.local, call: x }
          - { id: b, type: tool.local, call: y }
        edges:
          - { from: a, to: end }
          - { from: b, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert [issue.node_id for issue in issues if issue.rule == "reachable"] == ["b"]


def test_check__reports_a_node_that_cannot_reach_end(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: trap
        entry: a
        nodes:
          - { id: a, type: tool.local, call: x }
          - { id: b, type: tool.local, call: y }
        edges:
          - { from: a, to: b }
          - { from: b, to: b }
        """)

    # act
    issues = check(graph)

    # verify
    assert "terminates" in rules(issues)


def test_check__reports_an_entry_that_is_not_a_node(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: no-entry
        entry: missing
        nodes:
          - { id: a, type: tool.local, call: x }
        edges:
          - { from: a, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert rules(issues) == ["entry"]


def test_check__reports_a_path_that_names_no_node_or_slot(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: bad-path
        entry: a
        nodes:
          - { id: a, type: llm, prompt: "{{ $.ghost.value }}" }
        edges:
          - { from: a, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert "path" in rules(issues)


def test_check__reports_a_path_outside_the_declared_out_schema(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: bad-field
        entry: a
        nodes:
          - id: a
            type: tool.local
            call: x
            out:
              type: object
              properties: { subject: { type: string } }
          - { id: b, type: llm, prompt: "{{ $.a.body }}" }
        edges:
          - { from: a, to: b }
          - { from: b, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert [issue.node_id for issue in issues if issue.rule == "path"] == ["b"]


def test_check__reports_a_when_path_outside_the_declared_schema(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: bad-guard
        entry: a
        nodes:
          - id: a
            type: tool.local
            call: x
            out:
              type: object
              properties: { route: { type: string } }
        edges:
          - { from: a, to: end, when: $.a.folder == "action" }
          - { from: a, to: end, when: true }
        """)

    # act
    issues = check(graph)

    # verify
    assert "path" in rules(issues)


def test_check__reports_a_path_reading_into_a_resource_handle(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: peeking
        requires: { inbox: mailbox@1 }
        entry: a
        nodes:
          - { id: a, type: llm, prompt: "{{ $.inbox.password }}" }
        edges:
          - { from: a, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert "path" in rules(issues)


def test_check__reports_an_unbound_slot_when_bindings_are_given(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from(VALID_GRAPH)

    # act
    issues = check(graph, bindings={})

    # verify
    assert rules(issues) == ["unbound-slot"]


def test_check__reports_a_return_path_outside_the_declared_schema(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        skill: bad-return
        entry: a
        nodes:
          - id: a
            type: tool.local
            call: x
            out:
              type: object
              properties: { route: { type: string } }
        edges:
          - { from: a, to: end }
        return: { folder: $.a.folder }
        """)

    # act
    issues = check(graph)

    # verify
    assert "path" in rules(issues)


# endregion
