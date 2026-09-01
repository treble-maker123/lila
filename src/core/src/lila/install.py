"""The install tree: two roots, ``adapters/`` and ``skills/``, with the path as identity.

Both are ``<root>/<namespace>/<name>/`` and neither has a manifest — what a directory
holds says which it is, and where it sits says what it is called. A namespace is the
unit a repo clones to, so an install is a set of clones under two roots.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path as FilePath

type Namespace = str  # the first segment: one publisher, one clone, e.g. ``test``
type Ref = str  # ``<namespace>/<name>`` — where a directory sits, and what addresses it

SEGMENT = re.compile(r"^[a-z0-9-]+$")

# A directory -> the path identifying it, or None when it is not one of these at all.
type Marker = Callable[[FilePath], FilePath | None]


class InstallError(RuntimeError):
    """A directory in the install tree is misnamed, malformed, or neither thing."""


def _segment(path: FilePath) -> str:
    """One path segment, checked before it becomes half of a ref that gets parsed.

    Raises:
        InstallError: it is not ``[a-z0-9-]+``.
    """
    if not SEGMENT.match(path.name):
        raise InstallError(f"{path}: {path.name!r} is not a ref segment ([a-z0-9-]+)")
    return path.name


def scan(roots: tuple[FilePath, ...], marker: Marker, what: str) -> dict[Ref, FilePath]:
    """Every ``<namespace>/<name>/`` under the roots, earlier roots winning a ref clash.

    Depth is fixed at two. ``marker`` decides what a directory has to hold to count and
    returns the path that identifies it — the directory itself for an adapter, its
    ``skill.yaml`` for a skill.

    Raises:
        InstallError: a segment is malformed, or a directory at depth two is neither.
    """
    found: dict[Ref, FilePath] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for namespace in sorted(path for path in root.iterdir() if path.is_dir()):
            for directory in sorted(path for path in namespace.iterdir() if path.is_dir()):
                identity = marker(directory)
                if identity is None:
                    raise InstallError(f"{directory} is not {what}")
                found.setdefault(f"{_segment(namespace)}/{_segment(directory)}", identity)
    return found
