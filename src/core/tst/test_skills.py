"""Unit tests for lila.skills — the index, namespace-scoped assets, and ref resolution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path as FilePath

import pytest

from lila.install import InstallError
from lila.resources import Registry
from lila.skills import SkillError, asset_path, discover, resolve_skill

# region fixtures

FIXTURES = FilePath(__file__).parent / "fixtures" / "skills"
SkillFactory = Callable[..., FilePath]

GRAPH = "entry: a\nnodes: [{ id: a, type: llm, prompt: hi }]\nedges: [{ from: a, to: end }]\n"


@pytest.fixture
def skill(tmp_path: FilePath) -> SkillFactory:
    """Write one skill into a temporary root and return that root."""

    def build(file_name: str = "skill.yaml", name: str = "digest") -> FilePath:
        root = tmp_path / "skills"
        directory = root / "acme" / name
        directory.mkdir(parents=True)
        (directory / file_name).write_text(GRAPH)
        return root

    return build


# endregion

# region discover


def test_discover__addresses_a_skill_by_where_it_sits(skill: SkillFactory) -> None:
    # prepare
    root = skill()

    # act
    found = discover(root)

    # verify
    assert found == {"acme/digest": root / "acme" / "digest" / "skill.yaml"}


def test_discover__accepts_the_yml_spelling_as_an_alias(skill: SkillFactory) -> None:
    # prepare
    root = skill(file_name="skill.yml")

    # act
    found = discover(root)

    # verify
    assert sorted(found) == ["acme/digest"]


def test_discover__raises_when_one_directory_holds_both_spellings(skill: SkillFactory) -> None:
    # prepare
    root = skill()
    (root / "acme" / "digest" / "skill.yml").write_text(GRAPH)

    # act / verify
    with pytest.raises(SkillError, match="both skill.yaml"):
        discover(root)


def test_discover__raises_when_a_directory_holds_no_graph(tmp_path: FilePath) -> None:
    # prepare — a shared-asset directory belongs beside the skills, not among them
    (tmp_path / "skills" / "acme" / "shared").mkdir(parents=True)

    # act / verify
    with pytest.raises(InstallError, match="no skill.yaml"):
        discover(tmp_path / "skills")


# endregion

# region asset_path


def test_asset_path__resolves_a_sibling_within_the_namespace(skill: SkillFactory) -> None:
    # prepare
    root = skill()

    # act
    found = asset_path(root / "acme" / "digest", "../shared/style.md")

    # verify
    assert found == root / "acme" / "shared" / "style.md"


def test_asset_path__raises_when_the_path_escapes_the_namespace(skill: SkillFactory) -> None:
    # prepare
    root = skill()

    # act / verify
    with pytest.raises(SkillError, match="escapes acme/"):
        asset_path(root / "acme" / "digest", "../../other/style.md")


# endregion

# region resolve_skill


def test_resolve_skill__loads_an_installed_skill_by_ref() -> None:
    # prepare
    registry = Registry(skills=discover(FIXTURES))

    # act
    graph = resolve_skill(registry)("test/fetching")

    # verify — identity is the ref it was found under, not anything the file says
    assert graph.ref == "test/fetching"


def test_resolve_skill__raises_listing_what_is_installed_for_an_unknown_ref() -> None:
    # prepare
    resolve = resolve_skill(Registry(skills=discover(FIXTURES)))

    # act / verify
    with pytest.raises(RuntimeError, match="test/fetching"):
        resolve("test/nothing")


# endregion
