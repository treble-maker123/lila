"""Discovery and loading of extensions — the only place core learns what exists.

An extension is a directory with a ``lila.toml`` manifest beside ``resources/`` and
``skills/``. Install is a git clone into ``.lila/extensions/``; this module finds those
first, then the bundled ones, and turns both into a Registry. Loading executes
third-party Python in-process — install is the trust boundary (P9).
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as FilePath
from types import ModuleType

from lila.executor import Graph, RunError, SkillResolver, load_graph
from lila.ext import RESOURCE_MARK, TOOL_MARK, Tool, ToolName, TypeRef, tool_schemas
from lila.resources import Registry, SkillRef
from lila.values import Json

MANIFEST_NAME = "lila.toml"
RESOURCES_DIR = "resources"
SKILLS_DIR = "skills"
SKILL_SUFFIXES = (".yaml", ".yml")

type ExtensionName = str  # ``publisher/extension``, e.g. ``test/email``


class ExtensionError(RuntimeError):
    """An extension is malformed, or two extensions claim the same name."""


@dataclass(frozen=True, slots=True)
class Manifest:
    """What a ``lila.toml`` says: identity, and what this extension depends on."""

    name: ExtensionName
    version: int
    requires: tuple[ExtensionName, ...] = ()
    root: FilePath | None = None  # where it was found

    @property
    def ref(self) -> str:
        """``publisher/extension@version`` — the prefix every member is addressed under."""
        return f"{self.name}@{self.version}"

    def member(self, name: str) -> TypeRef:
        """The full ref of one member, e.g. ``test/email@1/imap``."""
        return f"{self.ref}/{name}"


def load_manifest(path: FilePath) -> Manifest:
    """Read one ``lila.toml``.

    Raises:
        ExtensionError: the file is missing, malformed, or lacks name/version.
    """
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ExtensionError(f"no {MANIFEST_NAME} at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ExtensionError(f"{path}: {exc}") from exc
    name, version = raw.get("name"), raw.get("version")
    if not isinstance(name, str) or "/" not in name:
        raise ExtensionError(f"{path}: name must be 'publisher/extension'")
    if not isinstance(version, int):
        raise ExtensionError(f"{path}: version must be an integer")
    requires = raw.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
        raise ExtensionError(f"{path}: requires must be a list of extension names")
    return Manifest(
        name=name,
        version=version,
        requires=tuple(str(item) for item in requires),
        root=path.parent,
    )


def discover(*roots: FilePath) -> list[Manifest]:
    """Every extension under the given roots, earlier roots winning on a name clash.

    Raises:
        ExtensionError: an extension is malformed.
    """
    found: dict[ExtensionName, Manifest] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for directory in sorted(root.iterdir()):
            manifest_path = directory / MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            manifest = load_manifest(manifest_path)
            found.setdefault(manifest.name, manifest)
    return list(found.values())


def load(*roots: FilePath) -> Registry:
    """Discover every extension under the roots and register what it declares.

    Raises:
        ExtensionError: an extension is malformed or its Python cannot be imported.
        ExtError: a resource or tool declares something with no derivable schema.
    """
    registry = Registry()
    for manifest in discover(*roots):
        install(manifest, registry)
    return registry


def install(manifest: Manifest, registry: Registry) -> None:
    """Register one extension's resource types, tools, and skills.

    Raises:
        ExtensionError: its Python cannot be imported, or a requirement is missing.
        ExtError: a resource or tool declares something with no derivable schema.
    """
    assert manifest.root is not None
    for required in manifest.requires:
        if not any(ref.startswith(f"{required}@") for ref in registry.types):
            raise ExtensionError(f"{manifest.name} requires {required}, which is not installed")
    for module_path in sorted((manifest.root / RESOURCES_DIR).glob("*.py")):
        _register_module(_import(manifest, module_path), manifest, registry)
    skills = manifest.root / SKILLS_DIR
    for skill_path in sorted(skills.iterdir()) if skills.is_dir() else []:
        if skill_path.suffix in SKILL_SUFFIXES:
            registry.skills[manifest.member(skill_path.stem)] = skill_path


def _import(manifest: Manifest, path: FilePath) -> ModuleType:
    """Import one extension module under a name that cannot collide with a package.

    Raises:
        ExtensionError: the module cannot be loaded or raised while importing.
    """
    module_name = f"lila_ext_{manifest.name.replace('/', '_')}_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ExtensionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # an extension is third-party code; report, don't crash
        del sys.modules[module_name]
        raise ExtensionError(f"{path}: {exc}") from exc
    return module


def _register_module(module: ModuleType, manifest: Manifest, registry: Registry) -> None:
    """Find the marked resource classes and tool functions in one module.

    Raises:
        ExtensionError: a tool's first parameter is not a resource in this extension.
        ExtError: a schema cannot be derived.
    """
    types_by_class: dict[type, TypeRef] = {}
    for value in vars(module).values():
        if isinstance(value, type) and getattr(value, RESOURCE_MARK, False):
            ref = manifest.member(value.__name__.lower())
            registry.types[ref] = value
            types_by_class[value] = ref
    for name, value in vars(module).items():
        if callable(value) and getattr(value, TOOL_MARK, False):
            built = _build_tool(name, value, types_by_class, manifest)
            registry.tools[(built.resource_type, built.name)] = built


def _build_tool(
    name: ToolName,
    fn: Callable[..., object],
    types_by_class: dict[type, TypeRef],
    manifest: Manifest,
) -> Tool:
    """Derive one tool's schemas and the resource type its first parameter names.

    Raises:
        ExtensionError: its first parameter is not a resource declared here.
        ExtError: a schema cannot be derived.
    """
    holder, args, result = tool_schemas(fn)
    ref = types_by_class.get(holder) if isinstance(holder, type) else None
    if ref is None:
        raise ExtensionError(
            f"{manifest.name}: tool {name!r} takes {holder!r}, which is not a resource here"
        )
    summary = (fn.__doc__ or "").strip().splitlines()
    return Tool(
        name=name,
        resource_type=ref,
        args=args,
        result=result,
        run=fn,
        description=summary[0] if summary else "",
    )


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
        return load_graph(path)

    return resolve
