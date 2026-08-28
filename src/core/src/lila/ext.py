"""The extension surface: everything an extension is allowed to import from core.

An extension author writes a ``@resource`` dataclass and ``@tool`` functions over it;
arg and result schemas are derived from the signatures, so nothing is declared twice.
The decorators only tag — ``lila.extensions`` reads the tags and builds the registry,
which is what keeps this module free of any dependency on the rest of core.
"""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import NewType, get_args, get_origin, get_type_hints

from lila.values import Json, JsonSchema

# region names

type TypeRef = str  # ``publisher/extension@version/member``, e.g. ``test/email@1/imap``
type ToolName = str  # a tool's own name inside its resource type, e.g. ``get_message``
type FieldName = str  # a field of a resource's config, e.g. ``host``
type ArgName = str  # a parameter of a tool

# endregion

# A config field holding a credential: read from the environment, never from a graph,
# and never rendered into a prompt or the record.
Secret = NewType("Secret", str)

RESOURCE_MARK = "__lila_resource__"
TOOL_MARK = "__lila_tool__"


class ExtError(TypeError):
    """An extension declares something the harness cannot derive a schema from."""


class ToolError(RuntimeError):
    """A tool failed. The one error an extension raises for a failed call."""


def resource[T: type](cls: T) -> T:
    """Mark a dataclass as a resource type: its fields are the config shape."""
    setattr(cls, RESOURCE_MARK, True)
    return cls


def tool[T: Callable[..., object]](fn: T) -> T:
    """Mark a function as a tool.

    A first parameter annotated with a ``@resource`` class declares the resource it
    needs; without one the tool is pure, and every parameter is an argument.
    """
    setattr(fn, TOOL_MARK, True)
    return fn


@dataclass(frozen=True, slots=True)
class Tool:
    """One named operation, with schemas derived from Python.

    ``resource_type`` is None for a pure tool: it declares no resource, so it reaches
    nothing outside its arguments and needs no stub to replay.
    """

    name: ToolName
    resource_type: TypeRef | None
    args: JsonSchema  # derived from the signature, minus the resource parameter
    result: JsonSchema | None  # derived from the return annotation
    # Called as run(handle, **args). Its return is third-party and unchecked until the
    # caller narrows it to Json and validates it against ``result``.
    run: Callable[..., object]
    description: str = ""  # the docstring, for an ``llm`` node's tool grants


@dataclass(frozen=True, slots=True)
class ConfigField:
    """One field of a resource's config: what it holds and where it comes from."""

    name: FieldName
    schema: JsonSchema
    required: bool  # no default, so config must supply it
    secret: bool  # declared ``Secret``, so it is read from the environment


# region derivation

_SCALARS: dict[object, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


def json_schema(annotation: object, what: str) -> JsonSchema:
    """A JSON Schema for one annotation — the scalars, lists, dicts, and TypedDicts.

    Raises:
        ExtError: the annotation is not something a JSON Schema can describe.
    """
    if isinstance(annotation, type) and getattr(annotation, "__supertype__", None) is not None:
        annotation = annotation.__supertype__  # a NewType, e.g. Secret
    if annotation is Secret:
        return {"type": "string"}
    if annotation in _SCALARS:
        return {"type": _SCALARS[annotation]}
    if typing.is_typeddict(annotation):
        return _typed_dict_schema(annotation, what)
    origin = get_origin(annotation)
    if origin in (list, tuple):
        (item,) = get_args(annotation) or (str,)
        return {"type": "array", "items": json_schema(item, what)}
    if origin is dict:
        return {"type": "object"}
    if origin in (typing.Union, types.UnionType):
        # Only ``X | None`` — an optional field, described by X.
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return json_schema(args[0], what)
    raise ExtError(f"{what}: cannot derive a schema for {annotation!r}")


def _typed_dict_schema(annotation: object, what: str) -> JsonSchema:
    """A TypedDict as an object schema. Every key is required — tools return all of them."""
    hints = get_type_hints(annotation)
    properties: dict[str, Json] = {name: json_schema(hint, what) for name, hint in hints.items()}
    return {"type": "object", "properties": properties, "required": list(hints)}


def tool_schemas(fn: Callable[..., object]) -> tuple[object | None, JsonSchema, JsonSchema | None]:
    """The resource annotation, the arg schema, and the result schema of a tool.

    The resource is the first parameter when it is annotated with a ``@resource`` class,
    and None otherwise — a pure tool, whose every parameter is an argument.

    Raises:
        ExtError: a parameter is unannotated, or uses a type no schema can describe.
    """
    hints = get_type_hints(fn)
    parameters = list(inspect.signature(fn).parameters.values())
    holder, rest = None, parameters
    if parameters and _is_resource(hints.get(parameters[0].name)):
        holder, rest = hints[parameters[0].name], parameters[1:]
    properties: dict[str, Json] = {}
    required: list[Json] = []
    for parameter in rest:
        if parameter.name not in hints:
            raise ExtError(f"{fn.__name__}.{parameter.name}: parameters need annotations")
        properties[parameter.name] = json_schema(hints[parameter.name], fn.__name__)
        if parameter.default is parameter.empty:
            required.append(parameter.name)
    args: JsonSchema = {"type": "object", "properties": properties, "required": required}
    returns = hints.get("return")
    result = None if returns is None else json_schema(returns, fn.__name__)
    return holder, args, result


def _is_resource(annotation: object) -> bool:
    """Whether an annotation is a class marked ``@resource``."""
    return isinstance(annotation, type) and getattr(annotation, RESOURCE_MARK, False)


def config_fields(cls: type) -> list[ConfigField]:
    """The config shape of a resource dataclass: one entry per field.

    Raises:
        ExtError: the class is not a dataclass, or a field's type has no schema.
    """
    if not dataclasses.is_dataclass(cls):
        raise ExtError(f"{cls.__name__}: a resource must be a dataclass")
    hints = get_type_hints(cls)
    fields: list[ConfigField] = []
    for field in dataclasses.fields(cls):
        annotation = hints[field.name]
        has_default = (
            field.default is not dataclasses.MISSING
            or field.default_factory is not dataclasses.MISSING
        )
        fields.append(
            ConfigField(
                name=field.name,
                schema=json_schema(annotation, cls.__name__),
                required=not has_default,
                secret=annotation is Secret,
            )
        )
    return fields


# endregion

__all__ = [
    "ConfigField",
    "ExtError",
    "Secret",
    "Tool",
    "ToolError",
    "ToolName",
    "TypeRef",
    "config_fields",
    "json_schema",
    "resource",
    "tool",
    "tool_schemas",
]
