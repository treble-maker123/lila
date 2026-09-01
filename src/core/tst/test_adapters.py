"""Unit tests for lila.adapters — discovery, loading, and what lands in the registry."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path as FilePath

import pytest

from lila.adapters import AdapterError, discover, load
from lila.install import InstallError

# region fixtures

FIXTURES = FilePath(__file__).parent / "fixtures" / "adapters"
AdapterFactory = Callable[..., FilePath]

MODULE = """
from dataclasses import dataclass

from lila.ext import resource, tool


@resource
@dataclass(frozen=True)
class Widget:
    size: int = 1


@tool
def spin(widget: Widget, turns: int) -> str:
    \"\"\"Spin it.\"\"\"
    return "spun " * turns
"""


@pytest.fixture
def adapter(tmp_path: FilePath) -> AdapterFactory:
    """Write one adapter into a temporary root and return that root."""

    def build(module: str | None = MODULE, name: str = "tools") -> FilePath:
        root = tmp_path / "adapters"
        directory = root / "acme" / name
        (directory / "code").mkdir(parents=True)
        if module is not None:
            (directory / "code" / "widget.py").write_text(module)
        return root

    return build


# endregion

# region discover


def test_discover__addresses_an_adapter_by_where_it_sits(adapter: AdapterFactory) -> None:
    # prepare
    root = adapter()

    # act
    found = discover(root)

    # verify
    assert sorted(found) == ["acme/tools"]


def test_discover__lets_the_first_root_win_a_ref_clash(adapter: AdapterFactory) -> None:
    # prepare — the install is searched before the bundled set
    root = adapter()

    # act
    found = discover(root, FIXTURES, root)

    # verify
    assert sorted(found) == ["acme/tools", "test/fixture"]


def test_discover__ignores_a_root_that_does_not_exist(tmp_path: FilePath) -> None:
    # act / verify
    assert discover(tmp_path / "nothing") == {}


def test_discover__raises_when_a_directory_holds_no_code(tmp_path: FilePath) -> None:
    # prepare
    (tmp_path / "adapters" / "acme" / "tools").mkdir(parents=True)

    # act / verify
    with pytest.raises(InstallError, match="no code/"):
        discover(tmp_path / "adapters")


def test_discover__raises_when_a_segment_is_not_a_ref_segment(adapter: AdapterFactory) -> None:
    # prepare
    root = adapter(name="My Tools")

    # act / verify
    with pytest.raises(InstallError, match="ref segment"):
        discover(root)


# endregion

# region load


def test_load__registers_a_resource_type_under_its_adapter_ref(adapter: AdapterFactory) -> None:
    # prepare
    root = adapter()

    # act
    registry = load(root)

    # verify
    assert sorted(registry.types) == ["acme/tools/widget"]


def test_load__derives_a_tools_schemas_from_its_signature(adapter: AdapterFactory) -> None:
    # prepare
    root = adapter()

    # act
    registry = load(root)

    # verify
    spin = registry.tool("acme/tools/widget", "spin")
    assert spin.args["required"] == ["turns"]
    assert spin.result == {"type": "string"}
    assert spin.description == "Spin it."


def test_load__raises_naming_the_file_when_a_module_does_not_import(
    adapter: AdapterFactory,
) -> None:
    # prepare
    root = adapter(module="import a_package_that_is_not_installed\n")

    # act / verify
    with pytest.raises(AdapterError, match="widget.py"):
        load(root)


def test_load__publishes_a_tool_with_no_resource_as_pure(adapter: AdapterFactory) -> None:
    # prepare
    root = adapter(
        module=(
            "from lila.ext import tool\n\n\n"
            "@tool\n"
            "def widen(text: str, by: int) -> str:\n"
            '    """Widen it."""\n'
            "    return text * by\n"
        )
    )

    # act
    registry = load(root)

    # verify
    assert registry.tools == {}
    assert registry.pure_tool("acme/tools/widen").resource_type is None


def test_load__raises_when_a_tool_takes_a_resource_this_module_does_not_publish(
    adapter: AdapterFactory,
) -> None:
    # prepare — ``del`` leaves the annotation bound to a resource the scan never sees,
    # which is what importing another adapter's resource type looks like from here.
    root = adapter(
        module=(
            "from dataclasses import dataclass\n\n"
            "from lila.ext import resource, tool\n\n\n"
            "@resource\n"
            "@dataclass(frozen=True)\n"
            "class Elsewhere:\n"
            "    size: int = 1\n\n\n"
            "@tool\n"
            "def spin(widget: Elsewhere, turns: int) -> str:\n"
            '    """Spin it."""\n'
            '    return "spun"\n\n\n'
            "del Elsewhere\n"
        )
    )

    # act / verify
    with pytest.raises(AdapterError, match="not a resource here"):
        load(root)


# endregion
