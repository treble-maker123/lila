"""Unit tests for lila.verification. Pure, no I/O."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path as FilePath

import pytest
import yaml

from lila.executor import Graph, parse_graph
from lila.extensions import load
from lila.verification import Issue, check

# region fixtures

GraphFactory = Callable[..., Graph]
FIXTURES = FilePath(__file__).parent / "fixtures"


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
          - { id: a, type: llm, prompt: one }
          - { id: a, type: llm, prompt: two }
        edges:
          - { from: a, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert "unique-node-id" in rules(issues)


def test_check__reports_an_edge_to_an_unknown_node(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: dangling
        entry: a
        nodes:
          - { id: a, type: llm, prompt: one }
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
          - { id: a, type: llm, prompt: one, out: { type: object } }
          - { id: b, type: llm, prompt: two }
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
          - { id: a, type: llm, prompt: one }
          - { id: b, type: llm, prompt: two }
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
          - { id: a, type: llm, prompt: one }
          - { id: b, type: llm, prompt: two }
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
          - { id: a, type: llm, prompt: one }
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
            type: llm
            prompt: one
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
            type: llm
            prompt: one
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


def test_check__reports_a_path_naming_a_resource(graph_from: GraphFactory) -> None:
    # prepare — $. is memory only, so a resource name resolves to nothing
    graph = graph_from("""
        skill: peeking
        resources: { inbox: test/fixture@1/mailbox }
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


def test_check__reports_an_unbound_resource_when_bindings_are_given(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from(VALID_GRAPH)

    # act
    issues = check(graph, bindings={})

    # verify
    assert rules(issues) == ["unbound-resource"]


def test_check__reports_a_return_path_outside_the_declared_schema(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        skill: bad-return
        entry: a
        nodes:
          - id: a
            type: llm
            prompt: one
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


def test_check__reports_a_tool_node_naming_an_undeclared_resource(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        skill: undeclared
        entry: a
        nodes:
          - { id: a, type: tool, resource: inbox, call: get_message }
        edges:
          - { from: a, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert rules(issues) == ["undeclared-resource"]


def test_check__accepts_a_tool_node_with_no_resource_when_the_pure_ref_exists(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        skill: pure
        entry: a
        nodes:
          - { id: a, type: tool, call: test/fixture@1/shout, args: { phrase: "hi" } }
        edges:
          - { from: a, to: end }
        """)

    # act
    issues = check(graph, registry=load(FIXTURES))

    # verify
    assert rules(issues) == []


def test_check__reports_a_pure_ref_that_names_nothing(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: pure
        entry: a
        nodes:
          - { id: a, type: tool, call: test/fixture@1/whisper }
        edges:
          - { from: a, to: end }
        """)

    # act
    issues = check(graph, registry=load(FIXTURES))

    # verify
    assert rules(issues) == ["unknown-tool"]


def test_check__reports_a_call_the_resource_type_does_not_have(
    graph_from: GraphFactory,
) -> None:
    # prepare — the rule needs a registry: without one, nothing knows what exists
    graph = graph_from(VALID_GRAPH.replace("call: get_message", "call: burn_inbox"))

    # act
    issues = check(graph, registry=load(FIXTURES))

    # verify
    assert rules(issues) == ["unknown-tool"]


def test_check__reports_a_path_outside_a_tools_own_result_schema(
    graph_from: GraphFactory,
) -> None:
    # prepare — get_message returns id and subject, so $.fetch.body is not there
    graph = graph_from(VALID_GRAPH.replace("{{ $.fetch.subject }}", "{{ $.fetch.body }}"))

    # act
    issues = check(graph, registry=load(FIXTURES))

    # verify
    assert [issue.node_id for issue in issues if issue.rule == "path"] == ["classify"]


MAP_GRAPH = """
skill: mapper
entry: fan
input:
  type: object
  properties:
    ids: { type: array, items: { type: string } }
nodes:
  - id: fan
    type: skill.run
    for_each: $.input.ids
    input: { message_id: $.each }
    graph:
      skill: child
      entry: work
      nodes:
        - id: work
          type: llm
          prompt: one
      edges:
        - { from: work, to: end }
edges:
  - { from: fan, to: end }
"""


def test_check__accepts_each_inside_a_mapped_skill_run(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from(MAP_GRAPH)

    # act
    issues = check(graph)

    # verify
    assert issues == []


def test_check__reports_each_outside_a_mapped_node(graph_from: GraphFactory) -> None:
    # prepare — no for_each, so nothing binds $.each
    graph = graph_from(MAP_GRAPH.replace("    for_each: $.input.ids\n", ""))

    # act
    issues = check(graph)

    # verify
    assert rules(issues) == ["path"]


def test_check__reports_a_for_each_path_that_names_nothing(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from(MAP_GRAPH.replace("$.input.ids", "$.nowhere.ids"))

    # act
    issues = check(graph)

    # verify
    assert rules(issues) == ["path"]


def test_check__reports_a_node_named_each(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from("""
        skill: reserved
        entry: each
        nodes:
          - id: each
            type: llm
            prompt: one
        edges:
          - { from: each, to: end }
        """)

    # act
    issues = check(graph)

    # verify
    assert rules(issues) == ["unique-node-id"]


# endregion
