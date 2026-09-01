"""Graph model, loader, path expressions, run memory/record, and the run loop.

One module on purpose: the interfaces are still moving, and splitting it later is
mechanical. The ``tool`` handler lives in lila.tools; the static check in
lila.verification.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path as FilePath
from typing import Literal, TypeGuard

import jsonschema
import yaml

from lila.ext import ToolName, TypeRef
from lila.model import GenerateOptions, Message, Model, Usage
from lila.resources import ArgName, Instance, Registry, ResourceName, SkillName, SkillRef
from lila.values import Json, JsonSchema, Yaml

# region names

# Every str in the model is one of these. Aliases, not NewTypes: this module's job is
# turning untyped YAML into the model, and NewType would mean wrapping at every field.
# ResourceName/ArgName/SkillRef live in lila.resources, ToolName/TypeRef in lila.ext.
type NodeId = str  # a node's id in its own graph; ``end`` is the reserved target
type ModelAlias = str  # resolved against RunContext.models — never a raw model id
type OutputName = str  # key in the graph's ``return:``
type FieldName = str  # key inside a value — a mapping key or a path's root
type PathText = str  # a ``$.`` path as written, e.g. ``$.classify.label``
type InputLabel = str  # what the record keys a read value by — a PathText, ArgName, or "prompt"
type WhenText = str  # an edge's ``when:`` rendered back to source, for the record
type PromptTemplate = str  # a prompt with ``{{ $. }}`` holes
type Comment = str  # free-form author note; never read by the run loop
type Description = str  # a skill's public one-line summary of what it does

# Json/JsonSchema/Yaml come from lila.values; this adds this module's own type.
# A structured field after compile_value: the same shape, with ``$.`` strings as Paths.
type Compiled = Path | str | int | float | bool | None | list[Compiled] | dict[str, Compiled]

# endregion

# region errors


class GraphError(ValueError):
    """A graph file is malformed. Carries the offending node id when there is one.

    TODO: carry source file and line too — graphs are YAML, and today a parse failure
    names neither. ``yaml.safe_load`` drops position info; the fix is a SafeLoader
    subclass overriding ``construct_mapping`` to record ``node.start_mark.line`` in an
    id()-keyed side table (the document stays alive through parse), then threading it
    through ``_require_mapping`` so errors read ``skill.yaml:42: classify: ...``.
    """

    def __init__(self, message: str, *, node_id: NodeId | None = None) -> None:
        """Build the error, prefixing the message with the node id when given."""
        self.node_id = node_id
        super().__init__(f"{node_id}: {message}" if node_id else message)


class RunError(RuntimeError):
    """A run failed. Carries the node it failed at when there is one."""

    def __init__(self, message: str, *, node_id: NodeId | None = None) -> None:
        """Build the error, prefixing the message with the node id when given."""
        self.node_id = node_id
        super().__init__(f"{node_id}: {message}" if node_id else message)


# endregion

# region paths


@dataclass(frozen=True, slots=True)
class Key:
    """``.name`` — a mapping key, or the node id a path starts at."""

    name: FieldName


@dataclass(frozen=True, slots=True)
class Index:
    """``[0]`` / ``[-1]`` — one element, or one execution when it leads a path."""

    value: int


@dataclass(frozen=True, slots=True)
class Every:
    """``[*]`` — every execution, in order."""


Segment = Key | Index | Every

_SEGMENT = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(-?\d+|\*)\]")
_TEMPLATE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Path:
    """A parsed ``$.a.b[0].c``. Parsed once at load, never per read."""

    segments: tuple[Segment, ...]  # the walk, in order; always starts with a Key
    text: PathText  # the source text, kept so errors and the record read as written

    def __str__(self) -> PathText:
        """The original path text."""
        return self.text


def parse_path(text: PathText) -> Path:
    """Parse ``$.a.b[0].c`` into segments.

    Raises:
        GraphError: the text is not a path, has bad syntax, or names nothing.
    """
    stripped = text.strip()
    if not stripped.startswith("$"):
        raise GraphError(f"not a path: {text!r}")
    segments: list[Segment] = []
    position = 1
    while position < len(stripped):
        match = _SEGMENT.match(stripped, position)
        if match is None:
            raise GraphError(f"bad path syntax at offset {position} in {text!r}")
        name, subscript = match.group(1), match.group(2)
        if name is not None:
            segments.append(Key(name))
        elif subscript == "*":
            segments.append(Every())
        else:
            segments.append(Index(int(subscript)))
        position = match.end()
    if not segments:
        raise GraphError(f"path names nothing: {text!r}")
    if not isinstance(segments[0], Key):
        raise GraphError(f"path must start with a name: {text!r}")
    return Path(segments=tuple(segments), text=stripped)


def is_path(value: Yaml | Json) -> TypeGuard[PathText]:
    """Whether a raw YAML value looks like a ``$.`` path."""
    return isinstance(value, str) and value.strip().startswith("$.")


def compile_value(value: Json) -> Compiled:
    """Recursively turn ``$.`` strings inside a structured field into Path objects.

    Raises:
        GraphError: a ``$.`` string is not a valid path.
    """
    if is_path(value):
        return parse_path(value)
    if isinstance(value, dict):
        return {key: compile_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [compile_value(item) for item in value]
    return value


def compile_mapping(raw: dict[str, Json]) -> dict[ArgName, Compiled]:
    """compile_value over a whole ``args:``/``input:`` mapping.

    Raises:
        GraphError: a ``$.`` string is not a valid path.
    """
    return {key: compile_value(value) for key, value in raw.items()}


def paths_in(value: Compiled) -> list[Path]:
    """Every Path reachable inside a compiled structured field."""
    if isinstance(value, Path):
        return [value]
    if isinstance(value, dict):
        return [path for item in value.values() for path in paths_in(item)]
    if isinstance(value, list):
        return [path for item in value for path in paths_in(item)]
    return []


def template_paths(template: PromptTemplate) -> list[Path]:
    """Every ``{{ $. }}`` path in a prompt. Only a path is legal inside the braces.

    Raises:
        GraphError: a hole does not hold a valid path.
    """
    return [parse_path(match.group(1)) for match in _TEMPLATE.finditer(template)]


# endregion

# region graph model

NodeType = Literal["llm", "tool", "skill.run"]
# What ``type:`` may say in a file: the canonical set plus the accepted graph.run spelling.
NodeSpelling = Literal["llm", "tool", "skill.run", "graph.run"]


@dataclass(frozen=True, slots=True)
class LlmConfig:
    """An ``llm`` node: one completion, decoded against the node's ``out:``."""

    prompt: PromptTemplate  # ``{{ $. }}`` holes are filled from run memory
    model: ModelAlias = "default"  # bound by the caller, so a graph names no vendor
    # Reasoning, off unless a node asks for it: it costs tokens and latency out of
    # proportion to most nodes' work. A ``$.`` path lets an earlier node decide.
    think: Compiled = False


@dataclass(frozen=True, slots=True)
class ToolConfig:
    """A ``tool`` node: one tool call, on a resource the graph declared or on nothing.

    Transport lives inside the tool's implementation, so an HTTP call, an MCP call, and
    a pure transform are all this node. Omitting ``resource:`` selects a pure tool, which
    reaches nothing outside its arguments; ``call:`` is then a full member ref.
    """

    resource: ResourceName | None  # declared in the graph's ``resources:``; None is pure
    call: ToolName  # a tool on that resource's type, or a pure tool's full member ref
    args: dict[ArgName, Compiled] = field(default_factory=dict)  # paths resolve per call


@dataclass(frozen=True, slots=True)
class SkillRunConfig:
    """A ``skill.run`` node: a nested graph, run with a memory of its own."""

    ref: SkillRef | None = None  # exactly one of ref:/graph: — resolved at run time
    graph: Graph | None = None  # exactly one of ref:/graph: — inlined at load
    input: dict[ArgName, Compiled] = field(default_factory=dict)  # becomes the child's ``$.input``
    # child resource name -> parent resource name; a handle crosses by name, never as a value
    resources: dict[ResourceName, ResourceName] = field(default_factory=dict)
    # A path to a list: the node runs one child per item, ``$.each``, and outputs the
    # list of their outputs. Fan-out lives in one node, so the run loop stays linear.
    for_each: Path | None = None


NodeConfig = LlmConfig | ToolConfig | SkillRunConfig


@dataclass(frozen=True, slots=True)
class Node:
    """One step: what to run and what its output must look like."""

    id: NodeId  # unique in its graph
    type: NodeType  # picks the loader, the config type, and the handler
    config: NodeConfig  # the node type's own fields, already compiled
    # Declared for llm and skill.run; a tool node has none — its tool declares the shape.
    out: JsonSchema | None = None  # validated after the handler; None means unchecked
    comment: Comment = ""  # author's note; never read by the run loop


# region predicates

CompareOp = Literal["==", "!=", "in"]


@dataclass(frozen=True, slots=True)
class Always:
    """``when: true`` — the fallback edge."""


@dataclass(frozen=True, slots=True)
class Truthy:
    """``when: $.a.b`` — the value at the path is truthy."""

    path: Path


@dataclass(frozen=True, slots=True)
class Comparison:
    """``when: $.a.b == "x"`` — the fixed operator set, not an expression language."""

    op: CompareOp
    left: Path  # always a path; a literal on the left is not legal
    right: Path | Json  # a path, or a JSON literal parsed at load


Predicate = Always | Truthy | Comparison

_COMPARISON = re.compile(r"^(?P<left>\$[^\s]*)\s+(?P<op>==|!=|in)\s+(?P<right>.+)$")


def parse_predicate(value: Yaml) -> Predicate:
    """Parse an edge ``when:`` — a fixed predicate set, not an expression language (P2).

    Raises:
        GraphError: the value is not one of the supported predicate forms.
    """
    if value is True:
        return Always()
    if not isinstance(value, str):
        raise GraphError(f"unsupported when: {value!r}")
    text = value.strip()
    if text == "true":
        return Always()
    if match := _COMPARISON.match(text):
        op = match.group("op")
        assert op in ("==", "!=", "in")
        right_text = match.group("right").strip()
        right = parse_path(right_text) if is_path(right_text) else _literal(right_text)
        return Comparison(op=op, left=parse_path(match.group("left")), right=right)
    if is_path(text):
        try:
            return Truthy(path=parse_path(text))
        except GraphError as exc:
            raise GraphError(f"unsupported when: {value!r}") from exc
    raise GraphError(f"unsupported when: {value!r}")


def _literal(text: str) -> Json:
    """Parse the right-hand side of a comparison as a JSON literal.

    Raises:
        GraphError: the text is not valid JSON.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GraphError(f"not a literal: {text!r}") from exc


# endregion


END: NodeId = "end"
EACH: NodeId = "each"  # the current item inside a mapped skill.run's ``input:``


def describe(predicate: Predicate | None) -> WhenText:
    """An edge's condition as written, for the record. None is an unguarded edge."""
    match predicate:
        case None | Always():
            return "true"
        case Truthy(path):
            return path.text
        case Comparison(op, left, right):
            # A literal is round-tripped through JSON, the way it was parsed.
            rendered = right.text if isinstance(right, Path) else json.dumps(right)
            return f"{left.text} {op} {rendered}"


@dataclass(frozen=True, slots=True)
class Edge:
    """A possible transition. Where a run can go — EdgeEntry records where it went."""

    source: NodeId
    target: NodeId  # a node id, or ``end``
    when: Predicate | None = None  # None is unguarded, the same as ``when: true``
    comment: Comment = ""  # author's note; never read by the run loop


@dataclass(frozen=True, slots=True)
class Graph:
    """A whole skill: its nodes, its edges, and the contract at its edges."""

    # Identity comes from outside the document: the ref it was found under, or
    # ``<parent-ref>#<node-id>`` for an inline subgraph, which has no file of its own.
    ref: SkillRef
    entry: NodeId  # where a run starts
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]  # file order is semantics — first match wins
    # What this skill does, in one line. Public, unlike ``comment:``: install lists it,
    # and a model choosing among skills reads it.
    description: Description = ""
    # ``resources:`` — what this skill needs, bound to instances at install
    resources: dict[ResourceName, TypeRef] = field(default_factory=dict)
    input: JsonSchema | None = None  # validated before the run
    output: JsonSchema | None = None  # validated after the run
    returns: dict[OutputName, Path] = field(default_factory=dict)  # ``return:`` — builds the output
    source: FilePath | None = None  # the file it was loaded from, when it came from one
    comment: Comment = ""  # author's note; never read by the run loop

    def node(self, node_id: NodeId) -> Node | None:
        """The node with this id, or None."""
        return next((node for node in self.nodes if node.id == node_id), None)

    def edges_from(self, node_id: NodeId) -> tuple[Edge, ...]:
        """Outgoing edges of a node, in file order."""
        return tuple(edge for edge in self.edges if edge.source == node_id)


# endregion

# region loader


def _require_mapping(value: Yaml, what: str, node_id: NodeId | None = None) -> dict[str, Yaml]:
    """Coerce a YAML value to a str-keyed mapping. YAML allows any scalar as a key.

    Raises:
        GraphError: the value is not a mapping.
    """
    if not isinstance(value, dict):
        raise GraphError(f"{what} must be a mapping", node_id=node_id)
    return {str(key): item for key, item in value.items()}


def _require_json(value: Yaml, what: str, node_id: NodeId | None = None) -> Json:
    """Narrow a YAML value to JSON, so a run never carries something it cannot record.

    Raises:
        GraphError: the value holds a date, binary, or set — YAML types JSON has not.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_require_json(item, what, node_id) for item in value]
    if isinstance(value, dict):
        return {str(key): _require_json(item, what, node_id) for key, item in value.items()}
    raise GraphError(f"{what} must be JSON — got {type(value).__name__}", node_id=node_id)


def _require_json_mapping(value: Yaml, what: str, node_id: NodeId | None = None) -> dict[str, Json]:
    """A mapping whose values are all JSON — a schema, or a field to compile.

    Raises:
        GraphError: the value is not a mapping, or holds a non-JSON YAML type.
    """
    return {
        key: _require_json(item, what, node_id)
        for key, item in _require_mapping(value, what, node_id).items()
    }


def _load_llm(raw: dict[str, Yaml], node_id: NodeId, ref: SkillRef) -> LlmConfig:
    """Load an ``llm`` node's config.

    Raises:
        GraphError: no prompt, a non-alias model, a bad path in the prompt, or a
            ``think:`` that is neither a boolean nor a ``$.`` path.
    """
    prompt = raw.get("prompt")
    if not isinstance(prompt, str):
        raise GraphError("llm node needs a prompt", node_id=node_id)
    template_paths(prompt)  # parse now so a bad path fails at load
    model = raw.get("model", "default")
    if not isinstance(model, str):
        raise GraphError("model: must be an alias", node_id=node_id)
    raw_think = raw.get("think", False)
    if not isinstance(raw_think, bool) and not is_path(raw_think):
        raise GraphError("think: must be true, false, or a $. path", node_id=node_id)
    think = compile_value(raw_think)
    return LlmConfig(prompt=prompt, model=model, think=think)


def _load_tool(raw: dict[str, Yaml], node_id: NodeId, ref: SkillRef) -> ToolConfig:
    """Load a ``tool`` node's ``resource:``/``call:``/``args:``. ``resource:`` is optional.

    Raises:
        GraphError: call: missing or not a string, resource: present but not a string,
            args: is not a mapping, or the node declares an ``out:`` the tool declares.
    """
    resource, call = raw.get("resource"), raw.get("call")
    if resource is not None and not isinstance(resource, str):
        raise GraphError("tool node's resource: must name a declared resource", node_id=node_id)
    if not isinstance(call, str):
        raise GraphError("tool node needs call:", node_id=node_id)
    if raw.get("out") is not None:
        raise GraphError("tool node cannot declare out: — its tool does", node_id=node_id)
    args = compile_mapping(_require_json_mapping(raw.get("args", {}), "args:", node_id))
    return ToolConfig(resource=resource, call=call, args=args)


def _load_skill_run(raw: dict[str, Yaml], node_id: NodeId, ref: SkillRef) -> SkillRunConfig:
    """Load a ``skill.run`` node's config, parsing an inline ``graph:`` if present.

    ``ref`` is the enclosing graph's, and an inline child is identified under it.

    Raises:
        GraphError: not exactly one of ref:/graph:, a bad ref:, a non-path in
            resources:, or a malformed inline graph.
    """
    child_ref, inline = raw.get("ref"), raw.get("graph")
    if (child_ref is None) == (inline is None):
        raise GraphError("skill.run needs exactly one of ref: or graph:", node_id=node_id)
    if child_ref is not None and not isinstance(child_ref, str):
        raise GraphError("ref: must be a skill ref or a path", node_id=node_id)
    graph = (
        parse_graph(_require_mapping(inline, "graph:", node_id), f"{ref}#{node_id}")
        if inline is not None
        else None
    )
    node_input = compile_mapping(_require_json_mapping(raw.get("input", {}), "input:", node_id))
    resources: dict[ResourceName, ResourceName] = {}
    for name, value in _require_mapping(raw.get("resources", {}), "resources:", node_id).items():
        if not isinstance(value, str) or is_path(value):
            raise GraphError(f"resources.{name} must name a declared resource", node_id=node_id)
        resources[name] = value
    raw_each = raw.get("for_each")
    if raw_each is not None and not is_path(raw_each):
        raise GraphError("for_each: must be a $. path", node_id=node_id)
    for_each = parse_path(str(raw_each)) if raw_each is not None else None
    return SkillRunConfig(
        ref=child_ref, graph=graph, input=node_input, resources=resources, for_each=for_each
    )


# Node type -> config loader. Adding a transport is one entry.
NODE_LOADERS: dict[NodeSpelling, Callable[[dict[str, Yaml], NodeId, SkillRef], NodeConfig]] = {
    "llm": _load_llm,
    "tool": _load_tool,
    "skill.run": _load_skill_run,
    "graph.run": _load_skill_run,  # a spelling of skill.run, normalized on load
}


def _load_node(raw: Yaml, ref: SkillRef) -> Node:
    """Load one node, dispatching on ``type:`` and normalizing graph.run to skill.run.

    ``ref`` is the enclosing graph's; only an inline subgraph reads it.

    Raises:
        GraphError: missing id, unknown type, a bad out: schema, or a bad config.
    """
    node_raw = _require_mapping(raw, "a node")
    node_id = node_raw.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise GraphError("node needs an id")
    node_type = node_raw.get("type")
    if not _is_spelling(node_type):
        raise GraphError(f"unknown node type {node_type!r}", node_id=node_id)
    config = NODE_LOADERS[node_type](node_raw, node_id, ref)
    out_raw = node_raw.get("out")
    out = _require_json_mapping(out_raw, "out:", node_id) if out_raw is not None else None
    canonical: NodeType = "skill.run" if node_type == "graph.run" else node_type
    return Node(id=node_id, type=canonical, config=config, out=out, comment=_comment(node_raw))


def _is_spelling(value: Yaml) -> TypeGuard[NodeSpelling]:
    """Whether ``type:`` names a node type this loader knows."""
    return isinstance(value, str) and value in NODE_LOADERS


def _comment(raw: dict[str, Yaml]) -> Comment:
    """A ``comment:`` note, if the author left one. Documentation only."""
    return str(raw.get("comment", ""))


def _load_edge(raw: Yaml) -> Edge:
    """Load one edge and parse its ``when:``.

    Raises:
        GraphError: from:/to: missing, or an unsupported when:.
    """
    edge_raw = _require_mapping(raw, "an edge")
    source, target = edge_raw.get("from"), edge_raw.get("to")
    if not isinstance(source, str) or not isinstance(target, str):
        raise GraphError(f"edge needs from: and to: — got {edge_raw!r}")
    when_raw = edge_raw.get("when")
    when = parse_predicate(when_raw) if when_raw is not None else None
    return Edge(source=source, target=target, when=when, comment=_comment(edge_raw))


def _schema(value: Yaml, what: str) -> JsonSchema | None:
    """A declared schema, or None when the graph declares none.

    Raises:
        GraphError: the schema is not a mapping, or holds a non-JSON YAML type.
    """
    return None if value is None else _require_json_mapping(value, what)


def parse_graph(
    raw: dict[str, Yaml],
    ref: SkillRef = "anonymous",
    source: FilePath | None = None,
) -> Graph:
    """Build a Graph from an already-parsed YAML document, stamped with its ref.

    The document names nothing about itself: ``ref`` is where it was found, which the
    caller knows and the file does not.

    Raises:
        GraphError: any part of the document is malformed.
    """
    entry = raw.get("entry")
    if not isinstance(entry, str):
        raise GraphError("graph needs entry:")
    nodes_list = raw.get("nodes")
    if not isinstance(nodes_list, list):
        raise GraphError("graph needs a nodes: list")
    edges_list = raw.get("edges", [])
    if not isinstance(edges_list, list):
        raise GraphError("edges: must be a list")

    resources: dict[ResourceName, TypeRef] = {
        name: str(type_ref)
        for name, type_ref in _require_mapping(raw.get("resources", {}), "resources:").items()
    }
    returns: dict[OutputName, Path] = {}
    for name, value in _require_mapping(raw.get("return", {}), "return:").items():
        if not is_path(value):
            raise GraphError(f"return.{name} must be a $. path")
        returns[name] = parse_path(str(value))

    return Graph(
        ref=ref,
        entry=entry,
        nodes=tuple(_load_node(node, ref) for node in nodes_list),
        edges=tuple(_load_edge(edge) for edge in edges_list),
        description=str(raw.get("description", "")).strip(),
        resources=resources,
        input=_schema(raw.get("input"), "input:"),
        output=_schema(raw.get("output"), "output:"),
        returns=returns,
        source=source,
        comment=_comment(raw),
    )


def load_graph(path: FilePath | str, ref: SkillRef | None = None) -> Graph:
    """Read a graph YAML file and parse it under the ref it was found at.

    A graph loaded by path rather than by ref is identified by that path — honest, and
    what ``lila check`` and the record then show.

    Raises:
        GraphError: the file is not a graph document or is malformed.
        OSError: the file cannot be read.
        yaml.YAMLError: the file is not valid YAML.
    """
    file_path = FilePath(path)
    raw = yaml.safe_load(file_path.read_text())
    if not isinstance(raw, dict):
        raise GraphError(f"{file_path} is not a graph document")
    return parse_graph(_require_mapping(raw, "the graph"), ref or str(file_path), file_path)


# endregion

# region run memory


class RunMemory:
    """Append-only history per node id, addressed by ``$.`` path.

    ``$.input`` is seeded at start. Bound resources ride along but are not addressable:
    ``$.`` is memory and only memory, so a handle can never reach a prompt or the record.
    """

    def __init__(
        self, run_input: Json, resources: Mapping[ResourceName, Instance] | None = None
    ) -> None:
        """Seed ``$.input`` with the run input and hold the resources bound for the run."""
        self._history: dict[NodeId, list[Json]] = {"input": [run_input]}
        self._resources: dict[ResourceName, Instance] = dict(resources or {})

    @property
    def resources(self) -> Mapping[ResourceName, Instance]:
        """Resources bound for this run, by the name the graph declared."""
        return self._resources

    def history(self, node_id: NodeId) -> list[Json]:
        """Every output this node produced, oldest first."""
        return list(self._history.get(node_id, []))

    def append(self, node_id: NodeId, value: Json) -> None:
        """Record one more execution of a node."""
        self._history.setdefault(node_id, []).append(value)

    def with_value(self, node_id: NodeId, value: Json) -> RunMemory:
        """A copy with one more name bound — how a map node exposes ``$.each``.

        The copy shares the same resources; writes to it do not reach this memory.
        """
        scoped = RunMemory(None, self._resources)
        scoped._history = {key: list(values) for key, values in self._history.items()}
        scoped._history[node_id] = [value]
        return scoped

    def snapshot(self) -> dict[NodeId, list[Json]]:
        """A copy of the whole history, for the record or a stub set."""
        return {node_id: list(values) for node_id, values in self._history.items()}

    def resolve(self, path: Path) -> Json:
        """Read one ``$.`` path out of memory.

        Raises:
            RunError: the path names nothing, or does not resolve against the stored
                value.
        """
        root = path.segments[0]
        assert isinstance(root, Key)
        if root.name not in self._history:
            raise RunError(f"{path} names nothing in run memory")
        return self._resolve_history(self._history[root.name], path.segments[1:], path)

    def _resolve_history(self, history: list[Json], rest: tuple[Segment, ...], path: Path) -> Json:
        """Pick the execution the path selects, then walk into it.

        Raises:
            RunError: the subscript is out of range or the walk does not resolve.
        """
        # A bare node id means the latest execution; an explicit subscript selects.
        if rest and isinstance(rest[0], Index | Every):
            selected, rest = self._subscript(history, rest[0], path), rest[1:]
        else:
            selected = history[-1]
        return self._walk(selected, rest, path)

    @staticmethod
    def _subscript(values: list[Json], segment: Segment, path: Path) -> Json:
        """Apply a leading ``[i]`` or ``[*]`` to a node's execution list.

        Raises:
            RunError: the index is out of range.
        """
        if isinstance(segment, Every):
            return list(values)
        assert isinstance(segment, Index)
        try:
            return values[segment.value]
        except IndexError as exc:
            raise RunError(f"{path} is out of range") from exc

    def _walk(self, value: Json, rest: tuple[Segment, ...], path: Path) -> Json:
        """Follow the remaining segments into a resolved value.

        Raises:
            RunError: a segment does not fit the value's shape, or an index is out
                of range.
        """
        current = value
        for segment in rest:
            match segment:
                case Key(name):
                    if not isinstance(current, dict) or name not in current:
                        raise RunError(f"{path} does not resolve")
                    current = current[name]
                case Index(index):
                    if not isinstance(current, list):
                        raise RunError(f"{path} indexes something that is not a list")
                    try:
                        current = current[index]
                    except IndexError as exc:
                        raise RunError(f"{path} is out of range") from exc
                case Every():
                    if not isinstance(current, list):
                        raise RunError(f"{path} maps over something that is not a list")
                    current = list(current)
        return current

    def resolve_mapping(self, field_value: dict[ArgName, Compiled]) -> dict[ArgName, Json]:
        """Resolve a whole ``args:``/``input:`` mapping.

        Raises:
            RunError: any path in the mapping does not resolve.
        """
        return {key: self.resolve_value(value) for key, value in field_value.items()}

    def resolve_value(self, value: Compiled) -> Json:
        """Resolve every Path inside a compiled structured field, to JSON.

        Raises:
            RunError: a path does not resolve, or names a slot.
        """
        if isinstance(value, Path):
            return self.resolve(value)
        if isinstance(value, dict):
            return {key: self.resolve_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve_value(item) for item in value]
        return value

    def render(self, template: PromptTemplate) -> str:
        """Fill ``{{ $. }}`` holes in a prompt. Only a path is legal inside the braces.

        Raises:
            GraphError: a hole does not hold a valid path.
            RunError: a hole's path does not resolve.
        """

        def substitute(match: re.Match[str]) -> str:
            """Replace one hole with its resolved value, JSON-encoding non-strings."""
            value = self.resolve(parse_path(match.group(1)))
            return value if isinstance(value, str) else json.dumps(value)

        return _TEMPLATE.sub(substitute, template)


# endregion

# region record


@dataclass(slots=True)
class NodeEntry:
    """One execution of a node. A node visited twice writes two entries."""

    node_id: NodeId
    type: NodeType
    inputs: dict[InputLabel, Json] = field(default_factory=dict)  # what the node read
    output: Json = None  # what it produced, after ``out:`` validation
    # Resources it touched, by name; handles and credentials never enter the record.
    resources: tuple[ResourceName, ...] = ()
    duration_ms: float = 0.0
    model: ModelAlias | None = None  # llm nodes only
    usage: Usage | None = None  # llm nodes only
    # Present for skill.run — the nested run's own record.
    child: RunRecord | None = None
    # Present for a mapped skill.run — one record per item, in order.
    children: list[RunRecord] = field(default_factory=list)


@dataclass(slots=True)
class EdgeEntry:
    """One evaluation of an edge. Edges tested and rejected are recorded too."""

    source: NodeId
    target: NodeId
    predicate: WhenText  # the ``when:`` rendered back to source, via describe()
    inputs: dict[InputLabel, Json] = field(default_factory=dict)  # what the predicate read
    taken: bool = False  # whether the run followed this edge


@dataclass(slots=True)
class RunRecord:
    """What one run did, in order — the artifact a replay and a report read."""

    skill: SkillRef  # the graph that ran, by the ref it was found under
    name: SkillName | None = None  # the instantiation it ran as; a nested run has none
    nodes: list[NodeEntry] = field(default_factory=list)
    edges: list[EdgeEntry] = field(default_factory=list)
    backend_version: str | None = None  # what produced the outputs, for replay

    def stub_set(self) -> dict[NodeId, list[Json]]:
        """Node outputs in order — the same structure a stub file is.

        Nested runs are not flattened; each skill.run entry keeps its own record.
        """
        stubs: dict[NodeId, list[Json]] = {}
        for entry in self.nodes:
            stubs.setdefault(entry.node_id, []).append(entry.output)
        return stubs


# endregion

# region run loop


@dataclass(frozen=True, slots=True)
class NodeResult:
    """What a handler returns: the output plus what the record wants to know."""

    output: Json  # goes to memory and the record, after ``out:`` validation
    inputs: dict[InputLabel, Json] = field(default_factory=dict)
    resources: tuple[ResourceName, ...] = ()
    model: ModelAlias | None = None
    usage: Usage | None = None
    child: RunRecord | None = None
    children: tuple[RunRecord, ...] = ()  # one per item, for a map node


@dataclass(frozen=True, slots=True)
class NodeCall:
    """What a handler is given: the node to run and the run around it."""

    node: Node
    memory: RunMemory  # read paths and render prompts through this
    context: RunContext


Handler = Callable[[NodeCall], Awaitable[NodeResult]]
SkillResolver = Callable[[SkillRef], Graph]


@dataclass(frozen=True, slots=True)
class RunContext:
    """Everything a run needs that is not the graph: handlers and the outside world."""

    handlers: dict[NodeType, Handler]  # node type -> what runs it
    models: dict[ModelAlias, Model] = field(default_factory=dict)  # binds ``model:`` aliases
    registry: Registry | None = None  # resource types, tools, instances
    skills: SkillResolver | None = None  # resolves a skill.run ``ref:``
    backend_version: str | None = None  # stamped onto the record
    # Guards a runaway loop; a graph has no other stop condition yet.
    max_steps: int = 100


@dataclass(frozen=True, slots=True)
class RunResult:
    """A finished run: what it returned, what it did, and what it remembered."""

    output: dict[OutputName, Json]  # built from ``return:``, validated against ``output:``
    record: RunRecord
    memory: RunMemory  # the full history, for a nested run or a test


def evaluate(predicate: Predicate, memory: RunMemory) -> tuple[bool, dict[InputLabel, Json]]:
    """Return whether the predicate holds, plus the values it read, for the record.

    Raises:
        RunError: a path in the predicate does not resolve.
    """
    match predicate:
        case Always():
            return True, {}
        case Truthy(path):
            value = memory.resolve(path)
            return bool(value), {path.text: value}
        case Comparison(op, left, right):
            left_value = memory.resolve(left)
            inputs: dict[InputLabel, Json] = {left.text: left_value}
            if isinstance(right, Path):
                right_value = memory.resolve(right)
                inputs[right.text] = right_value
            else:
                right_value = right
            match op:
                case "==":
                    return left_value == right_value, inputs
                case "!=":
                    return left_value != right_value, inputs
                case "in":
                    return _contains(left_value, right_value), inputs


def _contains(needle: Json, haystack: Json) -> bool:
    """``in`` over the containers JSON has. Anything else is False, not an error."""
    if isinstance(haystack, str):
        return isinstance(needle, str) and needle in haystack
    if isinstance(haystack, (list, dict)):
        return needle in haystack
    return False


def _validate(value: Json, schema: JsonSchema | None, what: str, node_id: NodeId | None) -> None:
    """Validate a value against a JSON Schema, doing nothing when none is declared.

    Raises:
        RunError: the value does not satisfy the schema.
        jsonschema.SchemaError: the declared schema is itself invalid.
    """
    if schema is None:
        return
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as exc:
        raise RunError(f"{what} failed validation: {exc.message}", node_id=node_id) from exc


def bind_resources(
    graph: Graph, resources: Mapping[ResourceName, Instance]
) -> dict[ResourceName, Instance]:
    """Resolve every declared resource once, at run start.

    Raises:
        RunError: a resource is unbound or bound to an instance of another type.
    """
    bound: dict[ResourceName, Instance] = {}
    for name, type_ref in graph.resources.items():
        instance = resources.get(name)
        if instance is None:
            raise RunError(f"resource {name!r} is unbound")
        if instance.type != type_ref:
            raise RunError(f"resource {name!r} wants {type_ref}, bound to {instance.type}")
        bound[name] = instance
    return bound


async def run(
    graph: Graph,
    run_input: dict[ArgName, Json],
    context: RunContext,
    resources: Mapping[ResourceName, Instance] | None = None,
    name: SkillName | None = None,
) -> RunResult:
    """Execute a graph. A nested run is this same function with a fresh memory.

    ``name`` is the instantiation this run was started as, recorded and nothing more; a
    nested run has none, since only the top of a run is something the install named.

    Raises:
        RunError: input/output validation fails, a resource is unbound, a node has no
            handler or fails, no edge matches, an edge points at an unknown node, or
            the run exceeds ``max_steps``.
    """
    _validate(run_input, graph.input, "graph input", None)
    memory = RunMemory(run_input, bind_resources(graph, resources or {}))
    record = RunRecord(skill=graph.ref, name=name, backend_version=context.backend_version)

    current = graph.entry
    steps = 0
    while current != END:
        node = graph.node(current)
        if node is None:
            raise RunError(f"edge points at unknown node {current!r}")
        steps += 1
        if steps > context.max_steps:
            raise RunError(f"run exceeded {context.max_steps} steps", node_id=node.id)
        await _execute(node, memory, context, record)
        current = _next_node(graph, node, memory, record)

    output = {name: memory.resolve(path) for name, path in graph.returns.items()}
    _validate(output, graph.output, "graph output", None)
    return RunResult(output=output, record=record, memory=memory)


async def _execute(node: Node, memory: RunMemory, context: RunContext, record: RunRecord) -> None:
    """Run one node's handler, validate its output, and append memory and record entries.

    Raises:
        RunError: no handler for the node type, the handler fails, or the output
            fails its ``out:`` schema.
    """
    handler = context.handlers.get(node.type)
    if handler is None:
        raise RunError(f"no handler for node type {node.type!r}", node_id=node.id)
    started = time.monotonic()
    result = await handler(NodeCall(node=node, memory=memory, context=context))
    _validate(result.output, node.out, "output", node.id)
    memory.append(node.id, result.output)
    record.nodes.append(
        NodeEntry(
            node_id=node.id,
            type=node.type,
            inputs=result.inputs,
            output=result.output,
            resources=result.resources,
            duration_ms=(time.monotonic() - started) * 1000,
            model=result.model,
            usage=result.usage,
            child=result.child,
            children=list(result.children),
        )
    )


def _next_node(graph: Graph, node: Node, memory: RunMemory, record: RunRecord) -> NodeId:
    """First edge whose ``when:`` passes. File order is semantics — see P2.

    Raises:
        RunError: no edge matched, or a predicate's path does not resolve.
    """
    for edge in graph.edges_from(node.id):
        when = describe(edge.when)
        if edge.when is None:
            record.edges.append(EdgeEntry(edge.source, edge.target, when, {}, True))
            return edge.target
        taken, inputs = evaluate(edge.when, memory)
        record.edges.append(EdgeEntry(edge.source, edge.target, when, inputs, taken))
        if taken:
            return edge.target
    raise RunError("no edge matched", node_id=node.id)


# endregion

# region handlers


async def llm_handler(call: NodeCall) -> NodeResult:
    """Render the prompt, decode against ``out:``, append the parsed object.

    Raises:
        RunError: the model alias is unbound, a prompt path does not resolve,
            ``think:`` does not resolve to a boolean, or the model did not return JSON
            when ``out:`` is declared.
    """
    node = call.node
    config = node.config
    assert isinstance(config, LlmConfig)
    model = call.context.models.get(config.model)
    if model is None:
        raise RunError(f"no model bound to alias {config.model!r}", node_id=node.id)
    think = call.memory.resolve_value(config.think)
    if not isinstance(think, bool):
        raise RunError(f"think: resolved to {think!r}, which is not a boolean", node_id=node.id)
    prompt = call.memory.render(config.prompt)
    completion = await model.complete(
        [Message(role="user", content=prompt)],
        GenerateOptions(json_schema=node.out, think=think),
    )
    try:
        output = json.loads(completion.text) if node.out is not None else completion.text
    except json.JSONDecodeError as exc:
        raise RunError(
            f"model did not return JSON: {completion.text[:200]!r}", node_id=node.id
        ) from exc
    return NodeResult(
        output=output,
        inputs={"prompt": prompt},
        model=model.name,
        usage=completion.usage,
    )


async def skill_run_handler(call: NodeCall) -> NodeResult:
    """Run a nested graph with a fresh memory built only from input:/resources:.

    With ``for_each:`` the node is a map: one child run per item, exposed to ``input:``
    as ``$.each``, and the node's output is the list of their outputs.

    Raises:
        RunError: ``ref:`` needs a resolver that is not bound, a ``resources:`` entry
            names something the parent did not declare, ``for_each:`` does not name a
            list, or a child run fails.
    """
    node = call.node
    config = node.config
    assert isinstance(config, SkillRunConfig)
    child_graph = config.graph
    if child_graph is None:
        if call.context.skills is None:
            raise RunError("no skill resolver bound", node_id=node.id)
        assert config.ref is not None
        child_graph = call.context.skills(config.ref)
    # The isolation rule falls out of not passing the parent's objects down.
    child_resources: dict[ResourceName, Instance] = {}
    for child_name, parent_name in config.resources.items():
        instance = call.memory.resources.get(parent_name)
        if instance is None:
            raise RunError(
                f"resources.{child_name} names {parent_name!r}, which this graph does not declare",
                node_id=node.id,
            )
        child_resources[child_name] = instance

    if config.for_each is None:
        child_input = call.memory.resolve_mapping(config.input)
        result = await run(child_graph, child_input, call.context, child_resources)
        return NodeResult(
            output=result.output,
            inputs=child_input,
            resources=tuple(child_resources),
            child=result.record,
        )

    items = call.memory.resolve(config.for_each)
    if not isinstance(items, list):
        raise RunError(f"for_each {config.for_each} does not name a list", node_id=node.id)
    outputs: list[Json] = []
    records: list[RunRecord] = []
    for item in items:
        scoped = call.memory.with_value(EACH, item)
        child_input = scoped.resolve_mapping(config.input)
        result = await run(child_graph, child_input, call.context, child_resources)
        outputs.append(result.output)
        records.append(result.record)
    return NodeResult(
        output=outputs,
        inputs={config.for_each.text: items},
        resources=tuple(child_resources),
        children=tuple(records),
    )


# endregion


def resolve_skill_path(root: FilePath) -> SkillResolver:
    """Resolve ``ref:`` as a path under ``root`` — the fallback when nothing is installed."""

    def resolve(ref: SkillRef) -> Graph:
        """Load the graph at ``root/ref``, or ``root/ref/skill.yaml`` for a directory.

        Raises:
            RunError: the ref names nothing under root.
            GraphError: the file is not a valid graph document.
        """
        candidate = root / ref
        if candidate.is_dir():
            candidate = candidate / "skill.yaml"
        if not candidate.exists():
            raise RunError(f"cannot resolve skill ref {ref!r}")
        return load_graph(candidate, ref)

    return resolve
