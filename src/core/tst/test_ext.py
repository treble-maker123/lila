"""Unit tests for lila.ext — the schemas an extension never has to write down."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

import pytest

from lila.ext import ExtError, Secret, config_fields, json_schema, resource, tool, tool_schemas

# region fixtures


@resource
@dataclass(frozen=True)
class Box:
    """A resource with one of each kind of field."""

    host: str
    password: Secret
    port: int = 993
    verbose: bool = False


class Item(TypedDict):
    """A structured result."""

    id: str
    tags: list[str]


@tool
def fetch(box: Box, id: str, limit: int = 10) -> Item:
    """Fetch one item.

    A second line the description should not carry.
    """
    return {"id": id, "tags": []}


def undecorated(box: Box, id) -> Item:  # type: ignore[no-untyped-def]
    """A tool that forgot to annotate a parameter."""
    return {"id": id, "tags": []}


# endregion

# region json_schema


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (str, {"type": "string"}),
        (int, {"type": "integer"}),
        (bool, {"type": "boolean"}),
        (float, {"type": "number"}),
        (Secret, {"type": "string"}),
        (list[str], {"type": "array", "items": {"type": "string"}}),
        (str | None, {"type": "string"}),
    ],
)
def test_json_schema__describes_the_kinds_a_tool_may_use(
    annotation: object, expected: dict[str, object]
) -> None:
    # act / verify
    assert json_schema(annotation, "what") == expected


def test_json_schema__describes_a_typed_dict_as_an_object_with_every_key_required() -> None:
    # act
    schema = json_schema(Item, "what")

    # verify
    assert schema == {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["id", "tags"],
    }


def test_json_schema__raises_when_the_annotation_is_not_describable() -> None:
    # act / verify
    with pytest.raises(ExtError, match="cannot derive a schema"):
        json_schema(object, "what")


# endregion

# region tool_schemas


def test_tool_schemas__derives_args_from_the_signature_minus_the_resource() -> None:
    # act
    holder, args, result = tool_schemas(fetch)

    # verify — a parameter with a default is optional
    assert holder is Box
    assert args == {
        "type": "object",
        "properties": {"id": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["id"],
    }
    assert result is not None and result["type"] == "object"


def test_tool_schemas__raises_when_a_parameter_is_unannotated() -> None:
    # act / verify
    with pytest.raises(ExtError, match="parameters need annotations"):
        tool_schemas(undecorated)


def test_tool_schemas__raises_when_there_is_no_resource_parameter() -> None:
    # prepare
    def no_resource() -> str:
        """Takes nothing."""
        return ""

    # act / verify
    with pytest.raises(ExtError, match="first parameter"):
        tool_schemas(no_resource)


# endregion

# region config_fields


def test_config_fields__reads_the_config_shape_off_the_dataclass() -> None:
    # act
    fields = {declared.name: declared for declared in config_fields(Box)}

    # verify
    assert fields["host"].required is True
    assert fields["port"].required is False
    assert fields["port"].schema == {"type": "integer"}


def test_config_fields__marks_the_secret_fields() -> None:
    # act
    fields = {declared.name: declared for declared in config_fields(Box)}

    # verify
    assert fields["password"].secret is True
    assert fields["host"].secret is False


def test_config_fields__raises_when_the_resource_is_not_a_dataclass() -> None:
    # prepare
    @resource
    class Plain:
        """Not a dataclass."""

    # act / verify
    with pytest.raises(ExtError, match="must be a dataclass"):
        config_fields(Plain)


# endregion
