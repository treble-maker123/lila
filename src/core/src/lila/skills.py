"""Discovery of skills — the index a ``ref:`` resolves against.

A skill is a directory holding ``skill.yaml``: one graph, plus whatever it reads beside
it. It lives at ``skills/<namespace>/<name>/`` and that path is its identity. Skills are
the only thing permitted to use more than one adapter, so they sit in their own tree
rather than inside one; a local, unpublished one is a directory here like any other.

This is an index of what exists, not a table of what runs — config says the latter.
"""

from __future__ import annotations

from pathlib import Path as FilePath

from lila.executor import Graph, RunError, SkillResolver, load_graph
from lila.install import InstallError, scan
from lila.resources import Registry, SkillRef

SKILL_FILE = "skill.yaml"
SKILL_ALIAS = "skill.yml"  # accepted, not canonical; both in one directory is an error


class SkillError(InstallError):
    """A skill directory is malformed, or two files claim to be its graph."""


def _marker(directory: FilePath) -> FilePath | None:
    """A directory is a skill iff it holds ``skill.yaml`` (or its alias).

    Raises:
        SkillError: it holds both spellings, so which one runs would be arbitrary.
    """
    canonical, alias = directory / SKILL_FILE, directory / SKILL_ALIAS
    if canonical.is_file() and alias.is_file():
        raise SkillError(f"{directory} has both {SKILL_FILE} and {SKILL_ALIAS}")
    if canonical.is_file():
        return canonical
    return alias if alias.is_file() else None


def discover(*roots: FilePath) -> dict[SkillRef, FilePath]:
    """Every skill under the given roots, mapped to its graph file.

    Earlier roots win a ref clash, so an install shadows a bundled skill of the same
    name.

    Raises:
        InstallError: a directory in the tree is not a skill.
    """
    return scan(roots, _marker, f"a skill — no {SKILL_FILE} in it")


def asset_path(skill: FilePath, relative: str) -> FilePath:
    """One file a skill reads beside itself, resolved within its own namespace.

    ``skill`` is the skill's directory. A sibling under the same namespace is reachable
    (``../shared/style.md``) because the namespace is what a repo clones to; anything
    above it belongs to someone else.

    Raises:
        SkillError: the path escapes the namespace directory.
    """
    namespace = skill.parent.resolve()
    resolved = (skill / relative).resolve()
    if not resolved.is_relative_to(namespace):
        raise SkillError(f"{relative!r} escapes {namespace.name}/")
    return resolved


def resolve_skill(registry: Registry) -> SkillResolver:
    """Resolve a ``ref:`` against the skills installed here."""

    def resolve(ref: SkillRef) -> Graph:
        """Load the graph a ref names.

        Raises:
            RunError: the ref names no installed skill.
            GraphError: the file is not a valid graph document.
        """
        path = registry.skills.get(ref)
        if path is None:
            raise RunError(
                f"cannot resolve skill ref {ref!r}; installed: {sorted(registry.skills)}"
            )
        return load_graph(path, ref)

    return resolve
