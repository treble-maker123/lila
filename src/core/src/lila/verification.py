"""Static check — the "compiled before it runs" half of the thesis.

Pure, no I/O. ``check(graph)`` is ``lila check <file>`` and the first half of a dry run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lila.executor import (
    EACH,
    END,
    Always,
    Comparison,
    Every,
    Graph,
    Index,
    Key,
    LlmConfig,
    Node,
    NodeId,
    Path,
    Segment,
    SkillRunConfig,
    ToolConfig,
    Truthy,
    paths_in,
    template_paths,
)
from lila.resources import InstanceName, Registry, ResourceName
from lila.values import Json, JsonSchema

type RuleName = str  # the check that produced an issue, e.g. ``unreachable-edge``


@dataclass(frozen=True, slots=True)
class Issue:
    """One reason a graph will not run. Every issue is fatal — there are no warnings."""

    rule: RuleName
    message: str
    node_id: NodeId | None = None  # absent for graph-wide issues, e.g. a bad entry

    def __str__(self) -> str:
        """One line: rule, node id when there is one, then the message."""
        return f"[{self.rule}] {self.node_id + ': ' if self.node_id else ''}{self.message}"


@dataclass(slots=True)
class _Context:
    """The graph under check plus the issues found so far, threaded through the rules."""

    graph: Graph
    registry: Registry | None = None  # what is installed, when the caller knows
    issues: list[Issue] = field(default_factory=list)

    def report(self, rule: RuleName, message: str, node_id: NodeId | None = None) -> None:
        """Record one issue."""
        self.issues.append(Issue(rule=rule, message=message, node_id=node_id))

    def result(self, node: Node) -> JsonSchema | None:
        """The schema a node's output must match: its ``out:``, or its tool's result."""
        if not isinstance(node.config, ToolConfig) or self.registry is None:
            return node.out
        type_ref = self.graph.resources.get(node.config.resource)
        if type_ref is None:
            return None
        tool = self.registry.tools.get((type_ref, node.config.call))
        return tool.result if tool is not None else None


def check(
    graph: Graph,
    bindings: dict[ResourceName, InstanceName] | None = None,
    registry: Registry | None = None,
) -> list[Issue]:
    """Every rule, in one pass. An empty list means the graph is runnable.

    ``bindings`` and ``registry`` are what an install knows: without them the
    binding and tool rules are skipped, and a tool node's output shape is unknown.
    """
    context = _Context(graph=graph, registry=registry)
    _check_ids(context)
    _check_edges(context)
    _check_reachability(context)
    _check_paths(context)
    _check_resources(context, bindings)
    _check_tools(context)
    return context.issues


# region rules


def _check_ids(context: _Context) -> None:
    """Node ids are unique and not reserved."""
    seen: set[NodeId] = set()
    for node in context.graph.nodes:
        if node.id in seen:
            context.report("unique-node-id", "duplicate node id", node.id)
        seen.add(node.id)
        if node.id in {END, EACH}:
            context.report("unique-node-id", f"{node.id!r} is a reserved name", node.id)


def _check_edges(context: _Context) -> None:
    """Edges name real nodes, and nothing sits behind an unguarded edge."""
    graph = context.graph
    ids = {node.id for node in graph.nodes} | {END}
    for edge in graph.edges:
        if edge.source not in {node.id for node in graph.nodes}:
            context.report("edge-target", f"edge from unknown node {edge.source!r}")
        if edge.target not in ids:
            context.report("edge-target", f"edge to unknown node {edge.target!r}", edge.source)
    # File order is semantics: nothing after an unguarded edge can ever be taken.
    for node in graph.nodes:
        outgoing = graph.edges_from(node.id)
        for index, edge in enumerate(outgoing):
            unguarded = edge.when is None or isinstance(edge.when, Always)
            if unguarded and index < len(outgoing) - 1:
                context.report(
                    "unreachable-edge",
                    f"edges after the unguarded edge to {edge.target!r} are dead",
                    node.id,
                )
                break


def _check_reachability(context: _Context) -> None:
    """Every node is reachable from entry, and can still reach ``end``."""
    graph = context.graph
    if graph.node(graph.entry) is None:
        context.report("entry", f"entry {graph.entry!r} is not a node")
        return
    forward = _reachable(graph, {graph.entry})
    for node in graph.nodes:
        if node.id not in forward:
            context.report("reachable", "not reachable from entry", node.id)
    # `end` reachable from every node, so no node can trap a run.
    ends: set[NodeId] = set()
    changed = True
    while changed:
        changed = False
        for edge in graph.edges:
            if (edge.target == END or edge.target in ends) and edge.source not in ends:
                ends.add(edge.source)
                changed = True
    for node in graph.nodes:
        if node.id in forward and node.id not in ends:
            context.report("terminates", "cannot reach end", node.id)


def _reachable(graph: Graph, seeds: set[NodeId]) -> set[NodeId]:
    """Node ids reachable from the seeds by following edges forward."""
    seen = set(seeds)
    queue = list(seeds)
    while queue:
        for edge in graph.edges_from(queue.pop()):
            if edge.target not in seen and edge.target != END:
                seen.add(edge.target)
                queue.append(edge.target)
    return seen


def _check_resources(context: _Context, bindings: dict[ResourceName, InstanceName] | None) -> None:
    """Every declared resource has a binding, when bindings were supplied at all."""
    if bindings is None:
        return
    for name in context.graph.resources:
        if name not in bindings:
            context.report("unbound-resource", f"resource {name!r} is unbound")


def _check_tools(context: _Context) -> None:
    """Every tool node names a declared resource, and a tool its type really has."""
    graph = context.graph
    for node in graph.nodes:
        config = node.config
        if not isinstance(config, ToolConfig):
            continue
        type_ref = graph.resources.get(config.resource)
        if type_ref is None:
            context.report(
                "undeclared-resource",
                f"resource {config.resource!r} is not in resources:",
                node.id,
            )
            continue
        if context.registry is None:
            continue
        if context.registry.tools.get((type_ref, config.call)) is None:
            known = sorted(context.registry.tools_of(type_ref))
            context.report(
                "unknown-tool", f"{type_ref} has no tool {config.call!r}; it has {known}", node.id
            )


def _check_paths(context: _Context) -> None:
    """Check every path the graph reads — node configs, returns, and predicates."""
    for node in context.graph.nodes:
        for path in _node_paths(node):
            # ``$.each`` exists only inside the map node that binds it.
            if _root(path) == EACH and _maps(node):
                continue
            _check_path(context, path, node.id)
    for name, path in context.graph.returns.items():
        _check_path(context, path, None, what=f"return.{name}")
    for edge in context.graph.edges:
        match edge.when:
            case Truthy(path):
                _check_path(context, path, edge.source)
            case Comparison(_, left, right):
                _check_path(context, left, edge.source)
                if isinstance(right, Path):
                    _check_path(context, right, edge.source)
            case _:
                pass


def _node_paths(node: Node) -> list[Path]:
    """Every path a node's config reads, whatever its type."""
    match node.config:
        case LlmConfig(prompt=prompt, think=think):
            return template_paths(prompt) + paths_in(think)
        case ToolConfig(args=args):
            return paths_in(args)
        case SkillRunConfig(input=node_input, for_each=for_each):
            # ``resources:`` names resources, not paths — nothing to resolve here.
            return paths_in(node_input) + ([for_each] if for_each is not None else [])


def _root(path: Path) -> NodeId:
    """The path's first segment — the node, slot, or reserved name it starts from."""
    root = path.segments[0]
    assert isinstance(root, Key)
    return root.name


def _maps(node: Node) -> bool:
    """Whether this node is a mapped skill.run, and so binds ``$.each``."""
    return isinstance(node.config, SkillRunConfig) and node.config.for_each is not None


def _check_path(context: _Context, path: Path, node_id: NodeId | None, what: str = "") -> None:
    """Every ``$.`` path resolves against a declared schema."""
    label = f"{what} " if what else ""
    root = path.segments[0]
    assert isinstance(root, Key)
    graph = context.graph
    if root.name == "input":
        _walk_schema(context, graph.input, path.segments[1:], path, node_id, label)
        return
    target = graph.node(root.name)
    if target is None:
        context.report("path", f"{label}{path} names no node or $.input", node_id)
        return
    rest = path.segments[1:]
    # A leading subscript selects an execution; anything else means the latest one.
    if rest and isinstance(rest[0], Index | Every):
        if isinstance(rest[0], Every):
            return  # a list of executions — element shape is checked per element elsewhere
        rest = rest[1:]
    _walk_schema(context, context.result(target), rest, path, node_id, label)


def _walk_schema(
    context: _Context,
    schema: JsonSchema | None,
    segments: tuple[Segment, ...],
    path: Path,
    node_id: NodeId | None,
    label: str,
) -> None:
    """Follow a path's segments through a JSON Schema, reporting a segment it lacks."""
    current: JsonSchema | None = schema
    for segment in segments:
        if current is None:
            return  # nothing declared to check against
        match segment:
            case Key(name):
                properties = current.get("properties")
                if not isinstance(properties, dict):
                    return
                if name not in properties:
                    context.report("path", f"{label}{path} is not in the declared schema", node_id)
                    return
                current = _subschema(properties[name])
            case Index() | Every():
                current = _subschema(current.get("items"))


def _subschema(value: Json) -> JsonSchema | None:
    """A nested schema, or None when the schema declares nothing there."""
    return value if isinstance(value, dict) else None


# endregion
