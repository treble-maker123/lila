"""Unit tests for lila.executor. No daemon, no network."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path as FilePath

import pytest
import yaml

from lila.executor import (
    Always,
    Comparison,
    Every,
    Graph,
    GraphError,
    Handler,
    Index,
    Key,
    NodeCall,
    NodeResult,
    RunContext,
    RunError,
    RunMemory,
    SkillRunConfig,
    ToolConfig,
    Truthy,
    describe,
    evaluate,
    llm_handler,
    load_graph,
    parse_graph,
    parse_path,
    parse_predicate,
    resolve_skill_path,
    run,
    skill_run_handler,
)
from lila.model import GenerateEvent, GenerateOptions, Message, Model, TextChunk, Usage
from lila.resources import Instance, SkillRef
from lila.values import Json

# region fixtures

GraphFactory = Callable[..., Graph]


class ScriptedModel(Model):
    """Backend replaying fixed completions, so an llm node needs no daemon."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.prompts: list[str] = []
        self.options: list[GenerateOptions | None] = []

    @property
    def name(self) -> str:
        return "scripted"

    async def generate(
        self,
        messages: list[Message],
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[GenerateEvent]:
        self.prompts.append(messages[-1].content)
        self.options.append(options)
        yield TextChunk(text=self.texts.pop(0))
        yield Usage(prompt_tokens=1, completion_tokens=2, done_reason="stop")


MAILBOX = "test/fixture/mailbox"


class FakeHandle:
    """Stands in for an adapter's own resource object; the loop never looks inside."""


def instance(name: str = "fake-inbox", type_ref: str = MAILBOX) -> Instance:
    """One configured resource, as an install would have built it."""
    return Instance(name=name, type=type_ref, handle=FakeHandle())


def echo_handler(value: Json) -> Handler:
    """Handler factory returning a fixed output for any node."""

    async def handler(call: NodeCall) -> NodeResult:
        return NodeResult(output=value)

    return handler


@pytest.fixture
def graph_from() -> GraphFactory:
    """Build a Graph from YAML source text, stamped with the ref it is known by."""

    def build(source: str, ref: SkillRef = "anonymous") -> Graph:
        return parse_graph(yaml.safe_load(source), ref)

    return build


LINEAR_GRAPH = """
entry: first
input:
  type: object
  properties:
    seed: { type: string }
nodes:
  - id: first
    type: tool
    resource: store
    call: echo
    args: { value: $.input.seed }
edges:
  - { from: first, to: end }
return:
  value: $.first.value
"""

# endregion

# region parse_path


def test_parse_path__splits_names_and_subscripts_when_path_is_nested() -> None:
    # prepare
    text = "$.classify[-2].scores[0].value"

    # act
    path = parse_path(text)

    # verify
    assert path.segments == (
        Key("classify"),
        Index(-2),
        Key("scores"),
        Index(0),
        Key("value"),
    )


def test_parse_path__keeps_star_as_every_when_path_selects_all_executions() -> None:
    # prepare
    text = "$.classify[*]"

    # act
    path = parse_path(text)

    # verify
    assert path.segments == (Key("classify"), Every())


def test_parse_path__raises_graph_error_when_text_is_not_a_path() -> None:
    # act / verify
    with pytest.raises(GraphError, match="not a path"):
        parse_path("classify.route")


def test_parse_path__raises_graph_error_when_syntax_is_malformed() -> None:
    # act / verify
    with pytest.raises(GraphError, match="bad path syntax"):
        parse_path("$.classify..route")


# endregion

# region RunMemory


def test_resolve__returns_latest_execution_when_path_has_no_subscript() -> None:
    # prepare
    memory = RunMemory({})
    memory.append("classify", {"route": "first"})
    memory.append("classify", {"route": "second"})

    # act
    value = memory.resolve(parse_path("$.classify.route"))

    # verify
    assert value == "second"


def test_resolve__returns_earlier_execution_when_path_indexes_backwards() -> None:
    # prepare
    memory = RunMemory({})
    memory.append("classify", {"route": "first"})
    memory.append("classify", {"route": "second"})

    # act
    value = memory.resolve(parse_path("$.classify[-2].route"))

    # verify
    assert value == "first"


def test_resolve__returns_every_execution_when_path_uses_star() -> None:
    # prepare
    memory = RunMemory({})
    memory.append("classify", {"route": "a"})
    memory.append("classify", {"route": "b"})

    # act
    value = memory.resolve(parse_path("$.classify[*]"))

    # verify
    assert value == [{"route": "a"}, {"route": "b"}]


def test_resolve__reads_the_run_input_when_path_starts_at_input() -> None:
    # prepare
    memory = RunMemory({"message_id": "42"})

    # act
    value = memory.resolve(parse_path("$.input.message_id"))

    # verify
    assert value == "42"


def test_resolve__does_not_reach_a_resource_when_a_path_names_one() -> None:
    # prepare — $. is memory and only memory, so a handle can never be read as a value
    memory = RunMemory({}, {"inbox": instance()})

    # act / verify
    with pytest.raises(RunError, match="names nothing"):
        memory.resolve(parse_path("$.inbox"))


def test_resolve__raises_run_error_when_node_has_no_history() -> None:
    # prepare
    memory = RunMemory({})

    # act / verify
    with pytest.raises(RunError, match="names nothing"):
        memory.resolve(parse_path("$.classify.route"))


def test_append__keeps_history_when_a_node_runs_twice() -> None:
    # prepare
    memory = RunMemory({})

    # act
    memory.append("loop", {"n": 1})
    memory.append("loop", {"n": 2})

    # verify
    assert memory.history("loop") == [{"n": 1}, {"n": 2}]


def test_render__substitutes_paths_when_prompt_has_holes() -> None:
    # prepare
    memory = RunMemory({})
    memory.append("fetch", {"subject": "hi", "count": 2})

    # act
    rendered = memory.render("s: {{ $.fetch.subject }} n: {{ $.fetch.count }}")

    # verify
    assert rendered == "s: hi n: 2"


def test_resolve_value__resolves_nested_paths_when_args_are_structured() -> None:
    # prepare
    memory = RunMemory({"id": "7"})
    args = {"id": parse_path("$.input.id"), "flags": ["seen", parse_path("$.input.id")]}

    # act
    resolved = memory.resolve_value(args)

    # verify
    assert resolved == {"id": "7", "flags": ["seen", "7"]}


# endregion

# region parse_predicate


def test_parse_predicate__returns_always_when_when_is_true() -> None:
    # act / verify
    assert parse_predicate(True) == Always()
    assert parse_predicate("true") == Always()


def test_parse_predicate__returns_comparison_when_when_compares_to_a_literal() -> None:
    # act
    predicate = parse_predicate('$.classify.route == "reply"')

    # verify
    assert predicate == Comparison(op="==", left=parse_path("$.classify.route"), right="reply")


def test_parse_predicate__returns_truthy_when_when_is_a_bare_path() -> None:
    # act
    predicate = parse_predicate("$.classify.route")

    # verify
    assert predicate == Truthy(path=parse_path("$.classify.route"))


def test_parse_predicate__raises_graph_error_when_when_is_an_expression() -> None:
    # act / verify
    with pytest.raises(GraphError, match="unsupported when"):
        parse_predicate("$.a.b + 1 > 2")


# endregion

# region describe


def test_describe__renders_true_when_the_edge_is_unguarded() -> None:
    # act / verify
    assert describe(None) == "true"
    assert describe(Always()) == "true"


def test_describe__renders_the_source_text_when_the_edge_is_guarded() -> None:
    # act / verify
    assert describe(parse_predicate("$.classify.route")) == "$.classify.route"
    assert describe(parse_predicate('$.classify.route == "reply"')) == '$.classify.route == "reply"'
    assert describe(parse_predicate("$.a.b in $.c.d")) == "$.a.b in $.c.d"


# endregion

# region evaluate


def test_evaluate__reports_the_values_read_when_comparison_holds() -> None:
    # prepare
    memory = RunMemory({})
    memory.append("classify", {"route": "reply"})

    # act
    taken, inputs = evaluate(parse_predicate('$.classify.route == "reply"'), memory)

    # verify
    assert taken is True
    assert inputs == {"$.classify.route": "reply"}


def test_evaluate__returns_false_when_comparison_does_not_hold() -> None:
    # prepare
    memory = RunMemory({})
    memory.append("classify", {"route": "flag"})

    # act
    taken, _ = evaluate(parse_predicate('$.classify.route == "reply"'), memory)

    # verify
    assert taken is False


def test_evaluate__returns_true_when_membership_holds() -> None:
    # prepare
    memory = RunMemory({})
    memory.append("classify", {"route": "flag"})

    # act
    taken, _ = evaluate(parse_predicate('$.classify.route in ["flag", "reply"]'), memory)

    # verify
    assert taken is True


# endregion

# region load_graph


def test_load_graph__builds_typed_nodes_when_file_declares_them(tmp_path: FilePath) -> None:
    # prepare
    path = tmp_path / "skill.yaml"
    path.write_text(LINEAR_GRAPH)

    # act
    graph = load_graph(path)

    # verify — nothing in the file says what it is called; the path it was read from does
    assert graph.ref == str(path)
    assert graph.entry == "first"
    assert isinstance(graph.nodes[0].config, ToolConfig)
    assert graph.returns["value"].text == "$.first.value"


def test_parse_graph__compiles_args_paths_when_node_is_a_tool(graph_from: GraphFactory) -> None:
    # prepare / act
    graph = graph_from(LINEAR_GRAPH)

    # verify
    config = graph.nodes[0].config
    assert isinstance(config, ToolConfig)
    assert config.args["value"] == parse_path("$.input.seed")


def test_parse_graph__normalizes_graph_run_to_skill_run(graph_from: GraphFactory) -> None:
    # prepare / act
    graph = graph_from("""
        entry: child
        nodes:
          - { id: child, type: graph.run, ref: other/thing }
        edges:
          - { from: child, to: end }
        """)

    # verify
    assert graph.nodes[0].type == "skill.run"
    assert isinstance(graph.nodes[0].config, SkillRunConfig)


def test_parse_graph__keeps_comments_when_the_author_left_them(graph_from: GraphFactory) -> None:
    # prepare / act
    graph = graph_from("""
        comment: the whole flow
        entry: first
        nodes:
          - { id: first, type: tool, resource: store, call: echo, comment: why this node }
        edges:
          - { from: first, to: end, comment: why this branch }
        """)

    # verify
    assert graph.comment == "the whole flow"
    assert graph.nodes[0].comment == "why this node"
    assert graph.edges[0].comment == "why this branch"


def test_parse_graph__reads_the_description_when_the_skill_declares_one(
    graph_from: GraphFactory,
) -> None:
    # prepare / act
    graph = graph_from("""
        description: >-
          Summarizes a mailbox
          in one line.
        entry: first
        nodes:
          - { id: first, type: llm, prompt: one }
        edges:
          - { from: first, to: end }
        """)

    # verify
    assert graph.description == "Summarizes a mailbox in one line."


def test_parse_graph__defaults_comments_to_empty_when_absent(graph_from: GraphFactory) -> None:
    # prepare / act
    graph = graph_from(LINEAR_GRAPH)

    # verify
    assert graph.comment == ""
    assert graph.description == ""
    assert graph.nodes[0].comment == ""
    assert graph.edges[0].comment == ""


def test_parse_graph__raises_graph_error_naming_the_node_when_type_is_unknown() -> None:
    # act / verify
    with pytest.raises(GraphError) as caught:
        parse_graph(
            {
                "entry": "a",
                "nodes": [{"id": "a", "type": "tool.smoke"}],
                "edges": [],
            }
        )
    assert caught.value.node_id == "a"


def test_parse_graph__raises_graph_error_when_args_hold_a_non_json_yaml_type(
    graph_from: GraphFactory,
) -> None:
    # act / verify — an unquoted date is a datetime.date, which no run can record
    with pytest.raises(GraphError, match="must be JSON — got date"):
        graph_from("""
            entry: first
            nodes:
              - { id: first, type: tool, resource: store, call: echo, args: { since: 2026-01-01 } }
            edges:
              - { from: first, to: end }
            """)


def test_parse_graph__raises_graph_error_when_a_tool_node_declares_out(
    graph_from: GraphFactory,
) -> None:
    # act / verify — the tool declares its result schema; the graph cannot disagree
    with pytest.raises(GraphError, match="its tool does"):
        graph_from("""
            entry: first
            nodes:
              - id: first
                type: tool
                resource: store
                call: echo
                out: { type: object }
            edges:
              - { from: first, to: end }
            """)


def test_parse_graph__raises_graph_error_when_skill_run_has_both_ref_and_graph() -> None:
    # act / verify
    with pytest.raises(GraphError, match="exactly one of"):
        parse_graph(
            {
                "entry": "a",
                "nodes": [{"id": "a", "type": "skill.run", "ref": "x/y", "graph": {}}],
                "edges": [],
            }
        )


# endregion

# region run


async def test_run__projects_memory_to_output_when_graph_returns(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from(LINEAR_GRAPH)
    context = RunContext(
        handlers={"tool": echo_handler({"value": "seeded"})},
    )

    # act
    result = await run(graph, {"seed": "seeded"}, context)

    # verify
    assert result.output == {"value": "seeded"}


async def test_run__takes_the_first_matching_edge_when_several_are_guarded(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        entry: classify
        nodes:
          - id: classify
            type: tool
            resource: store
            call: fixed
          - id: draft
            type: tool
            resource: store
            call: fixed
        edges:
          - { from: classify, to: draft, when: $.classify.route == "reply" }
          - { from: classify, to: end, when: true }
          - { from: draft, to: end }
        """)
    context = RunContext(handlers={"tool": echo_handler({"route": "reply"})})

    # act
    result = await run(graph, {}, context)

    # verify
    assert [entry.node_id for entry in result.record.nodes] == ["classify", "draft"]


async def test_run__falls_through_to_the_unguarded_edge_when_no_guard_matches(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        entry: classify
        nodes:
          - id: classify
            type: tool
            resource: store
            call: fixed
          - id: draft
            type: tool
            resource: store
            call: fixed
        edges:
          - { from: classify, to: draft, when: $.classify.route == "reply" }
          - { from: classify, to: end, when: true }
          - { from: draft, to: end }
        """)
    context = RunContext(handlers={"tool": echo_handler({"route": "flag"})})

    # act
    result = await run(graph, {}, context)

    # verify
    assert [entry.node_id for entry in result.record.nodes] == ["classify"]


async def test_run__raises_run_error_naming_the_node_when_output_breaks_its_schema(
    graph_from: GraphFactory,
) -> None:
    # prepare — only llm nodes declare out:; a tool node's schema comes from its tool
    graph = graph_from("""
        entry: first
        nodes:
          - id: first
            type: llm
            prompt: anything
            out:
              type: object
              properties: { value: { type: string } }
              required: [value]
        edges:
          - { from: first, to: end }
        """)
    context = RunContext(handlers={"llm": echo_handler({"value": 7})})

    # act / verify
    with pytest.raises(RunError) as caught:
        await run(graph, {}, context)
    assert caught.value.node_id == "first"


async def test_run__raises_run_error_when_graph_input_breaks_its_schema(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from(LINEAR_GRAPH)
    context = RunContext(handlers={"tool": echo_handler({"value": "x"})})

    # act / verify
    with pytest.raises(RunError, match="graph input"):
        await run(graph, {"seed": 7}, context)


async def test_run__raises_run_error_when_a_node_loops_past_max_steps(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        entry: spin
        nodes:
          - { id: spin, type: tool, resource: store, call: fixed }
        edges:
          - { from: spin, to: spin }
        """)
    context = RunContext(handlers={"tool": echo_handler({})}, max_steps=3)

    # act / verify
    with pytest.raises(RunError, match="exceeded 3 steps"):
        await run(graph, {}, context)


async def test_run__records_every_edge_with_its_evaluated_inputs(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        entry: classify
        nodes:
          - { id: classify, type: tool, resource: store, call: fixed }
        edges:
          - { from: classify, to: end, when: $.classify.route == "reply" }
          - { from: classify, to: end, when: true }
        """)
    context = RunContext(handlers={"tool": echo_handler({"route": "flag"})})

    # act
    result = await run(graph, {}, context)

    # verify
    assert [(edge.taken, edge.inputs) for edge in result.record.edges] == [
        (False, {"$.classify.route": "flag"}),
        (True, {}),
    ]


RESOURCE_GRAPH = """
resources: { inbox: test/fixture/mailbox }
entry: fetch
nodes:
  - { id: fetch, type: tool, resource: inbox, call: get_message, args: { id: "1" } }
edges:
  - { from: fetch, to: end }
"""


async def test_run__records_resources_by_name_when_a_node_uses_one(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from(RESOURCE_GRAPH)

    async def handler(call: NodeCall) -> NodeResult:
        config = call.node.config
        assert isinstance(config, ToolConfig)
        assert config.resource is not None
        return NodeResult(output={}, resources=(config.resource,))

    context = RunContext(handlers={"tool": handler})

    # act
    result = await run(graph, {}, context, {"inbox": instance()})

    # verify — the name, never the instance or its handle
    assert result.record.nodes[0].resources == ("inbox",)


async def test_run__raises_run_error_when_a_declared_resource_is_unbound(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from(RESOURCE_GRAPH)
    context = RunContext(handlers={"tool": echo_handler({})})

    # act / verify
    with pytest.raises(RunError, match="unbound"):
        await run(graph, {}, context)


async def test_run__raises_run_error_when_an_instance_is_of_another_type(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from(RESOURCE_GRAPH)
    context = RunContext(handlers={"tool": echo_handler({})})

    # act / verify
    with pytest.raises(RunError, match="test/fixture/calendar"):
        await run(graph, {}, context, {"inbox": instance(type_ref="test/fixture/calendar")})


async def test_run__records_the_stub_set_shape_when_a_node_runs_twice(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        entry: a
        nodes:
          - { id: a, type: tool, resource: store, call: fixed }
          - { id: b, type: tool, resource: store, call: fixed }
        edges:
          - { from: a, to: b }
          - { from: b, to: end }
        """)
    context = RunContext(handlers={"tool": echo_handler({"n": 1})})

    # act
    result = await run(graph, {}, context)

    # verify
    assert result.record.stub_set() == {"a": [{"n": 1}], "b": [{"n": 1}]}


# endregion

# region llm_handler


async def test_llm_handler__constrains_decoding_to_the_out_schema(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        entry: classify
        input: { type: object, properties: { subject: { type: string } } }
        nodes:
          - id: classify
            type: llm
            prompt: "Subject: {{ $.input.subject }}"
            out:
              type: object
              properties: { route: { type: string } }
              required: [route]
        edges:
          - { from: classify, to: end }
        return: { route: $.classify.route }
        """)
    model = ScriptedModel(['{"route": "reply"}'])
    context = RunContext(handlers={"llm": llm_handler}, models={"default": model})

    # act
    result = await run(graph, {"subject": "hello"}, context)

    # verify
    assert result.output == {"route": "reply"}
    assert model.prompts == ["Subject: hello"]
    assert model.options[0] is not None
    assert model.options[0].json_schema == graph.nodes[0].out


THINKING_GRAPH = """
entry: answer
input: { type: object, properties: { deep: { type: boolean } } }
nodes:
  - id: answer
    type: llm
    prompt: "go"
    think: THINK
    out:
      type: object
      properties: { text: { type: string } }
      required: [text]
edges:
  - { from: answer, to: end }
return: { text: $.answer.text }
"""


async def test_llm_handler__does_not_think_when_the_node_says_nothing(
    graph_from: GraphFactory,
) -> None:
    # prepare — reasoning costs tokens and latency, so it is opt-in
    graph = graph_from(THINKING_GRAPH.replace("    think: THINK\n", ""))
    model = ScriptedModel(['{"text": "hi"}'])
    context = RunContext(handlers={"llm": llm_handler}, models={"default": model})

    # act
    await run(graph, {}, context)

    # verify
    assert model.options[0] is not None
    assert model.options[0].think is False


async def test_llm_handler__thinks_when_the_node_asks_for_it(graph_from: GraphFactory) -> None:
    # prepare
    graph = graph_from(THINKING_GRAPH.replace("THINK", "true"))
    model = ScriptedModel(['{"text": "hi"}'])
    context = RunContext(handlers={"llm": llm_handler}, models={"default": model})

    # act
    await run(graph, {}, context)

    # verify
    assert model.options[0] is not None
    assert model.options[0].think is True


async def test_llm_handler__takes_think_from_a_path_when_the_graph_decides_it(
    graph_from: GraphFactory,
) -> None:
    # prepare — an earlier node, or the run input, chooses how hard to think
    graph = graph_from(THINKING_GRAPH.replace("THINK", "$.input.deep"))
    model = ScriptedModel(['{"text": "hi"}'])
    context = RunContext(handlers={"llm": llm_handler}, models={"default": model})

    # act
    await run(graph, {"deep": True}, context)

    # verify
    assert model.options[0] is not None
    assert model.options[0].think is True


async def test_llm_handler__raises_run_error_when_think_resolves_to_a_non_boolean(
    graph_from: GraphFactory,
) -> None:
    # prepare — declared as a string, so the schema lets it through to the handler
    source = THINKING_GRAPH.replace("THINK", "$.input.deep").replace(
        "{ deep: { type: boolean } }", "{ deep: { type: string } }"
    )
    graph = graph_from(source)
    model = ScriptedModel(['{"text": "hi"}'])
    context = RunContext(handlers={"llm": llm_handler}, models={"default": model})

    # act / verify
    with pytest.raises(RunError, match="not a boolean"):
        await run(graph, {"deep": "yes"}, context)


def test_parse_graph__raises_graph_error_when_think_is_not_a_boolean_or_path() -> None:
    # act / verify
    with pytest.raises(GraphError, match="think:"):
        parse_graph(yaml.safe_load(THINKING_GRAPH.replace("THINK", "sometimes")))


async def test_llm_handler__records_usage_and_model_when_the_call_succeeds(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        entry: classify
        nodes:
          - id: classify
            type: llm
            prompt: "hi"
            out: { type: object, properties: { route: { type: string } } }
        edges:
          - { from: classify, to: end }
        """)
    context = RunContext(handlers={"llm": llm_handler}, models={"default": ScriptedModel(["{}"])})

    # act
    result = await run(graph, {}, context)

    # verify
    entry = result.record.nodes[0]
    assert entry.model == "scripted"
    assert entry.usage == Usage(prompt_tokens=1, completion_tokens=2, done_reason="stop")


async def test_llm_handler__raises_run_error_when_the_model_returns_non_json(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        entry: classify
        nodes:
          - id: classify
            type: llm
            prompt: "hi"
            out: { type: object }
        edges:
          - { from: classify, to: end }
        """)
    context = RunContext(
        handlers={"llm": llm_handler}, models={"default": ScriptedModel(["not json"])}
    )

    # act / verify
    with pytest.raises(RunError, match="did not return JSON"):
        await run(graph, {}, context)


async def test_llm_handler__raises_run_error_when_the_alias_is_unbound(
    graph_from: GraphFactory,
) -> None:
    # prepare
    graph = graph_from("""
        entry: classify
        nodes:
          - { id: classify, type: llm, model: big, prompt: "hi" }
        edges:
          - { from: classify, to: end }
        """)
    context = RunContext(handlers={"llm": llm_handler})

    # act / verify
    with pytest.raises(RunError, match="alias 'big'"):
        await run(graph, {}, context)


# endregion

# region skill_run_handler

CHILD_GRAPH = """
entry: work
input:
  type: object
  properties:
    subject: { type: string }
nodes:
  - id: work
    type: tool
    resource: store
    call: fixed
edges:
  - { from: work, to: end }
return:
  note: $.work.note
"""


async def test_skill_run__lands_the_child_output_at_the_node_id(
    graph_from: GraphFactory, tmp_path: FilePath
) -> None:
    # prepare
    (tmp_path / "child.yaml").write_text(CHILD_GRAPH)
    parent = graph_from("""
        entry: gather
        nodes:
          - id: gather
            type: skill.run
            ref: child.yaml
            input: { subject: "hi" }
        edges:
          - { from: gather, to: end }
        return: { note: $.gather.note }
        """)
    context = RunContext(
        handlers={"skill.run": skill_run_handler, "tool": echo_handler({"note": "seen"})},
        skills=resolve_skill_path(tmp_path),
    )

    # act
    result = await run(parent, {}, context)

    # verify
    assert result.output == {"note": "seen"}


async def test_skill_run__hangs_the_child_record_off_the_parent_entry(
    graph_from: GraphFactory,
) -> None:
    # prepare
    parent = graph_from("""
        entry: gather
        nodes:
          - id: gather
            type: skill.run
            input: {}
            graph:
              entry: work
              nodes:
                - { id: work, type: tool, resource: store, call: fixed }
              edges:
                - { from: work, to: end }
        edges:
          - { from: gather, to: end }
        """)
    context = RunContext(
        handlers={"skill.run": skill_run_handler, "tool": echo_handler({"note": "seen"})},
    )

    # act
    result = await run(parent, {}, context)

    # verify
    child = result.record.nodes[0].child
    assert child is not None
    assert [entry.node_id for entry in child.nodes] == ["work"]


async def test_skill_run__keeps_the_child_from_reading_the_parent_memory(
    graph_from: GraphFactory,
) -> None:
    # prepare — the child asks for a parent node it was not handed
    parent = graph_from("""
        entry: seed
        nodes:
          - { id: seed, type: tool, resource: store, call: fixed }
          - id: gather
            type: skill.run
            input: {}
            graph:
              entry: work
              nodes:
                - { id: work, type: tool, resource: store, call: peek, args: { seen: $.seed.note } }
              edges:
                - { from: work, to: end }
        edges:
          - { from: seed, to: gather }
          - { from: gather, to: end }
        """)

    async def peek(call: NodeCall) -> NodeResult:
        config = call.node.config
        assert isinstance(config, ToolConfig)
        return NodeResult(output=call.memory.resolve_value(config.args))

    context = RunContext(handlers={"skill.run": skill_run_handler, "tool": peek})

    # act / verify
    with pytest.raises(RunError, match="names nothing"):
        await run(parent, {}, context)


async def test_skill_run__passes_a_resource_down_by_name_when_the_node_maps_resources(
    graph_from: GraphFactory,
) -> None:
    # prepare — the child names it ``box``, the parent ``inbox``; no path is involved
    parent = graph_from("""
        resources: { inbox: test/fixture/mailbox }
        entry: gather
        nodes:
          - id: gather
            type: skill.run
            resources: { box: inbox }
            input: {}
            graph:
              resources: { box: test/fixture/mailbox }
              entry: work
              nodes:
                - { id: work, type: tool, resource: box, call: get_message, args: { id: "1" } }
              edges:
                - { from: work, to: end }
              return: { instance: $.work.instance }
        edges:
          - { from: gather, to: end }
        return: { instance: $.gather.instance }
        """)
    bound = instance()

    async def tool(call: NodeCall) -> NodeResult:
        config = call.node.config
        assert isinstance(config, ToolConfig)
        assert config.resource is not None
        handle = call.memory.resources.get(config.resource)
        assert handle is not None
        return NodeResult(output={"instance": handle.name})

    context = RunContext(handlers={"skill.run": skill_run_handler, "tool": tool})

    # act
    result = await run(parent, {}, context, {"inbox": bound})

    # verify
    assert result.output == {"instance": "fake-inbox"}


async def test_skill_run__raises_run_error_when_a_mapped_resource_is_not_declared(
    graph_from: GraphFactory,
) -> None:
    # prepare
    parent = graph_from("""
        entry: gather
        nodes:
          - id: gather
            type: skill.run
            resources: { box: inbox }
            graph:
              entry: work
              nodes:
                - { id: work, type: tool, resource: box, call: get_message }
              edges:
                - { from: work, to: end }
        edges:
          - { from: gather, to: end }
        """)
    context = RunContext(handlers={"skill.run": skill_run_handler, "tool": echo_handler({})})

    # act / verify
    with pytest.raises(RunError, match="does not declare"):
        await run(parent, {}, context)


MAP_PARENT = """
entry: fan
nodes:
  - id: fan
    type: skill.run
    for_each: $.input.subjects
    input: { subject: $.each }
    graph:
      entry: work
      input:
        type: object
        properties:
          subject: { type: string }
      nodes:
        - id: work
          type: tool
          resource: store
          call: echo
          args: { subject: $.input.subject }
      edges:
        - { from: work, to: end }
      return: { subject: $.work.subject }
edges:
  - { from: fan, to: end }
return: { subjects: $.fan }
"""


async def echo_args(call: NodeCall) -> NodeResult:
    """A tool.local that hands back whatever args it was given."""
    config = call.node.config
    assert isinstance(config, ToolConfig)
    return NodeResult(output=call.memory.resolve_mapping(config.args))


async def test_skill_run__runs_one_child_per_item_when_for_each_is_set(
    graph_from: GraphFactory,
) -> None:
    # prepare
    parent = graph_from(MAP_PARENT)
    context = RunContext(handlers={"skill.run": skill_run_handler, "tool": echo_args})

    # act
    result = await run(parent, {"subjects": ["one", "two", "three"]}, context)

    # verify — the node's output is the list of child outputs, in order
    assert result.output == {
        "subjects": [{"subject": "one"}, {"subject": "two"}, {"subject": "three"}]
    }


async def test_skill_run__keeps_one_child_record_per_item_when_mapping(
    graph_from: GraphFactory,
) -> None:
    # prepare
    parent = graph_from(MAP_PARENT, "test/parent")
    context = RunContext(handlers={"skill.run": skill_run_handler, "tool": echo_args})

    # act
    result = await run(parent, {"subjects": ["one", "two"]}, context)

    # verify — an inline child has no file, so it is named under the node that holds it
    entry = result.record.nodes[0]
    assert entry.child is None
    assert [record.skill for record in entry.children] == ["test/parent#fan"] * 2


async def test_skill_run__runs_nothing_when_the_mapped_list_is_empty(
    graph_from: GraphFactory,
) -> None:
    # prepare
    parent = graph_from(MAP_PARENT)
    context = RunContext(handlers={"skill.run": skill_run_handler, "tool": echo_args})

    # act
    result = await run(parent, {"subjects": []}, context)

    # verify
    assert result.output == {"subjects": []}
    assert result.record.nodes[0].children == []


async def test_skill_run__raises_run_error_when_for_each_does_not_name_a_list(
    graph_from: GraphFactory,
) -> None:
    # prepare
    parent = graph_from(MAP_PARENT)
    context = RunContext(handlers={"skill.run": skill_run_handler, "tool": echo_args})

    # act / verify
    with pytest.raises(RunError, match="does not name a list"):
        await run(parent, {"subjects": "one"}, context)


async def test_skill_run__does_not_leak_each_into_the_parent_memory(
    graph_from: GraphFactory,
) -> None:
    # prepare
    parent = graph_from(MAP_PARENT)
    context = RunContext(handlers={"skill.run": skill_run_handler, "tool": echo_args})

    # act
    result = await run(parent, {"subjects": ["one"]}, context)

    # verify
    assert "each" not in result.memory.snapshot()


# endregion
