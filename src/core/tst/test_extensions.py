"""Unit tests for lila.extensions — discovery, loading, and what lands in the registry."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path as FilePath

import pytest

from lila.extensions import ExtensionError, discover, load, load_manifest, resolve_skill

# region fixtures

FIXTURES = FilePath(__file__).parent / "fixtures"
ExtensionFactory = Callable[..., FilePath]

MANIFEST = """
name = "acme/tools"
version = 2
"""

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
def extension(tmp_path: FilePath) -> ExtensionFactory:
    """Write one extension into a temporary root and return that root."""

    def build(manifest: str = MANIFEST, module: str | None = MODULE, skill: str = "") -> FilePath:
        root = tmp_path / "extensions"
        directory = root / "an-extension"
        (directory / "code").mkdir(parents=True)
        (directory / "lila.toml").write_text(manifest)
        if module is not None:
            (directory / "code" / "widget.py").write_text(module)
        if skill:
            (directory / "skills").mkdir()
            (directory / "skills" / "spin.yaml").write_text(skill)
        return root

    return build


# endregion

# region load_manifest


def test_load_manifest__reads_identity_and_builds_member_refs(extension: ExtensionFactory) -> None:
    # prepare
    root = extension()

    # act
    manifest = load_manifest(root / "an-extension" / "lila.toml")

    # verify
    assert manifest.ref == "acme/tools@2"
    assert manifest.member("widget") == "acme/tools@2/widget"


def test_load_manifest__raises_when_the_name_has_no_publisher(
    extension: ExtensionFactory,
) -> None:
    # prepare
    root = extension(manifest='name = "tools"\nversion = 1\n')

    # act / verify
    with pytest.raises(ExtensionError, match="publisher/extension"):
        load_manifest(root / "an-extension" / "lila.toml")


# endregion

# region discover


def test_discover__finds_every_directory_with_a_manifest(extension: ExtensionFactory) -> None:
    # prepare
    root = extension()

    # act
    found = discover(root)

    # verify
    assert [manifest.name for manifest in found] == ["acme/tools"]


def test_discover__lets_the_first_root_win_a_name_clash(extension: ExtensionFactory) -> None:
    # prepare — the install is searched before the bundled set
    root = extension()

    # act
    found = discover(root, FIXTURES, root)

    # verify
    assert sorted(manifest.name for manifest in found) == ["acme/tools", "test/fixture"]


def test_discover__ignores_a_root_that_does_not_exist(tmp_path: FilePath) -> None:
    # act / verify
    assert discover(tmp_path / "nothing") == []


# endregion

# region load


def test_load__registers_a_resource_type_under_its_extension_ref(
    extension: ExtensionFactory,
) -> None:
    # prepare
    root = extension()

    # act
    registry = load(root)

    # verify
    assert sorted(registry.types) == ["acme/tools@2/widget"]


def test_load__derives_a_tools_schemas_from_its_signature(extension: ExtensionFactory) -> None:
    # prepare
    root = extension()

    # act
    registry = load(root)

    # verify
    spin = registry.tool("acme/tools@2/widget", "spin")
    assert spin.args["required"] == ["turns"]
    assert spin.result == {"type": "string"}
    assert spin.description == "Spin it."


def test_load__registers_the_skills_beside_the_resources(extension: ExtensionFactory) -> None:
    # prepare
    root = extension(skill="skill: spinning\nentry: a\nnodes: []\n")

    # act
    registry = load(root)

    # verify
    assert sorted(registry.skills) == ["acme/tools@2/spin"]


def test_load__raises_naming_the_file_when_a_module_does_not_import(
    extension: ExtensionFactory,
) -> None:
    # prepare
    root = extension(module="import a_package_that_is_not_installed\n")

    # act / verify
    with pytest.raises(ExtensionError, match="widget.py"):
        load(root)


def test_load__publishes_a_tool_with_no_resource_as_pure(extension: ExtensionFactory) -> None:
    # prepare
    root = extension(
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
    assert registry.pure_tool("acme/tools@2/widen").resource_type is None


def test_load__raises_when_a_tool_takes_a_resource_this_module_does_not_publish(
    extension: ExtensionFactory,
) -> None:
    # prepare — ``del`` leaves the annotation bound to a resource the scan never sees,
    # which is what importing another extension's resource type looks like from here.
    root = extension(
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
    with pytest.raises(ExtensionError, match="not a resource here"):
        load(root)


def test_load__raises_when_a_requirement_is_not_installed(extension: ExtensionFactory) -> None:
    # prepare
    root = extension(manifest='name = "acme/tools"\nversion = 2\nrequires = ["other/thing"]\n')

    # act / verify
    with pytest.raises(ExtensionError, match="other/thing"):
        load(root)


# endregion

# region resolve_skill


def test_resolve_skill__loads_an_installed_skill_by_ref() -> None:
    # prepare
    registry = load(FIXTURES)

    # act
    graph = resolve_skill(registry)("test/fixture@1/fetch")

    # verify
    assert graph.skill == "fetching"


def test_resolve_skill__raises_listing_what_is_installed_for_an_unknown_ref() -> None:
    # prepare
    resolve = resolve_skill(load(FIXTURES))

    # act / verify
    with pytest.raises(RuntimeError, match="test/fixture@1/fetch"):
        resolve("test/fixture@1/nothing")


# endregion
