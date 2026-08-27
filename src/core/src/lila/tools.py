"""The ``tool`` node handler — the one path every tool call takes.

Four steps, no per-provider branching: resolve ``resource:`` to the bound instance, look
up ``call:`` in that resource type's tools, validate args, call and validate the result.
Transport lives inside the tool's implementation (lila.ext), so an IMAP fetch, an HTTP
call, and a pure transform all arrive here the same way.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable

import jsonschema

from lila.executor import (
    Handler,
    NodeCall,
    NodeResult,
    NodeType,
    RunError,
    ToolConfig,
    llm_handler,
    skill_run_handler,
)
from lila.ext import ToolError
from lila.resources import ResourceError
from lila.values import Json


async def tool_handler(call: NodeCall) -> NodeResult:
    """Run one tool against the instance bound to the node's ``resource:``.

    Raises:
        RunError: the resource is unbound, no registry is bound, the tool does not
            exist, the args do not fit its schema, or the tool itself failed.
    """
    node = call.node
    config = node.config
    assert isinstance(config, ToolConfig)
    instance = call.memory.resources.get(config.resource)
    if instance is None:
        raise RunError(f"resource {config.resource!r} is not bound", node_id=node.id)
    if call.context.registry is None:
        raise RunError("no registry bound; nothing defines tools", node_id=node.id)
    try:
        tool = call.context.registry.tool(instance.type, config.call)
    except ResourceError as exc:
        raise RunError(str(exc), node_id=node.id) from exc

    args = call.memory.resolve_mapping(config.args)
    try:
        jsonschema.validate(args, tool.args)
    except jsonschema.ValidationError as exc:
        raise RunError(f"args failed {config.call} schema: {exc.message}", node_id=node.id) from exc

    returned = await _invoke(tool.run, instance.handle, args, node.id)
    output = _as_json(returned, config.call, node.id)
    if tool.result is not None:
        try:
            jsonschema.validate(output, tool.result)
        except jsonschema.ValidationError as exc:
            raise RunError(
                f"{config.call} returned something its schema rejects: {exc.message}",
                node_id=node.id,
            ) from exc
    return NodeResult(output=output, inputs=args, resources=(config.resource,))


def _as_json(value: object, call: str, node_id: str) -> Json:
    """Narrow what a tool returned to JSON — it is third-party, so it is checked.

    Raises:
        RunError: the value holds something a run could not record.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_as_json(item, call, node_id) for item in value]
    if isinstance(value, dict):
        return {str(key): _as_json(item, call, node_id) for key, item in value.items()}
    raise RunError(f"{call} returned {type(value).__name__}, which is not JSON", node_id=node_id)


async def _invoke(
    run: Callable[..., object], handle: object, args: dict[str, Json], node_id: str
) -> object:
    """Call a tool, off the event loop when it is blocking.

    Raises:
        RunError: the tool raised.
    """
    try:
        if inspect.iscoroutinefunction(run):
            return await run(handle, **args)
        return await asyncio.to_thread(run, handle, **args)
    except ToolError as exc:
        raise RunError(str(exc), node_id=node_id) from exc
    except TypeError as exc:
        raise RunError(f"cannot call tool: {exc}", node_id=node_id) from exc


def default_handlers() -> dict[NodeType, Handler]:
    """Node type -> handler. One entry per node type; transports are not node types."""
    return {
        "llm": llm_handler,
        "tool": tool_handler,
        "skill.run": skill_run_handler,
    }
