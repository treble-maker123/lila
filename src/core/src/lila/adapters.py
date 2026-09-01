"""Discovery and loading of adapters — the only place core learns what a resource is.

An adapter is a directory holding ``code/``: the resource types it declares and the
tools over them, plus tools that need no resource at all. It lives at
``adapters/<namespace>/<adapter>/`` and that path is its identity — no manifest, no
declared requirements, and nothing it depends on but core. Install is a git clone of a
namespace into ``.lila/adapters/``; this module finds those first, then the bundled
ones, and turns both into a Registry. Loading executes third-party Python in-process —
install is the trust boundary (P9).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path as FilePath
from types import ModuleType

from lila.ext import RESOURCE_MARK, TOOL_MARK, Tool, ToolName, TypeRef, tool_schemas
from lila.install import InstallError, scan
from lila.resources import Registry

CODE_DIR = "code"

type AdapterRef = str  # ``<namespace>/<adapter>``, e.g. ``test/email``


class AdapterError(InstallError):
    """An adapter is malformed, or its Python cannot be imported."""


def member(ref: AdapterRef, name: str) -> TypeRef:
    """The full ref of one member, e.g. ``test/email/imap``."""
    return f"{ref}/{name}"


def _marker(directory: FilePath) -> FilePath | None:
    """A directory is an adapter iff it holds ``code/``."""
    return directory if (directory / CODE_DIR).is_dir() else None


def discover(*roots: FilePath) -> dict[AdapterRef, FilePath]:
    """Every adapter under the given roots, earlier roots winning a ref clash.

    Raises:
        InstallError: a directory in the tree is not an adapter.
    """
    return scan(roots, _marker, f"an adapter — no {CODE_DIR}/ in it")


def load(*roots: FilePath) -> Registry:
    """Discover every adapter under the roots and register what it declares.

    Raises:
        InstallError: the tree is malformed or an adapter's Python cannot be imported.
        ExtError: a resource or tool declares something with no derivable schema.
    """
    registry = Registry()
    for ref, root in discover(*roots).items():
        install(ref, root, registry)
    return registry


def install(ref: AdapterRef, root: FilePath, registry: Registry) -> None:
    """Register one adapter's resource types and tools.

    Raises:
        AdapterError: its Python cannot be imported.
        ExtError: a resource or tool declares something with no derivable schema.
    """
    for module_path in sorted((root / CODE_DIR).glob("*.py")):
        _register_module(_import(ref, module_path), ref, registry)


def _import(ref: AdapterRef, path: FilePath) -> ModuleType:
    """Import one adapter module under a name that cannot collide with a package.

    Raises:
        AdapterError: the module cannot be loaded or raised while importing.
    """
    module_name = f"lila_adapter_{ref.replace('/', '_')}_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AdapterError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # an adapter is third-party code; report, don't crash
        del sys.modules[module_name]
        raise AdapterError(f"{path}: {exc}") from exc
    return module


def _register_module(module: ModuleType, ref: AdapterRef, registry: Registry) -> None:
    """Find the marked resource classes and tool functions in one module.

    Raises:
        AdapterError: a tool names a resource that is not declared in this adapter.
        ExtError: a schema cannot be derived.
    """
    types_by_class: dict[type, TypeRef] = {}
    for value in vars(module).values():
        if isinstance(value, type) and getattr(value, RESOURCE_MARK, False):
            type_ref = member(ref, value.__name__.lower())
            registry.types[type_ref] = value
            types_by_class[value] = type_ref
    for name, value in vars(module).items():
        if callable(value) and getattr(value, TOOL_MARK, False):
            built = _build_tool(name, value, types_by_class, ref)
            if built.resource_type is None:
                registry.pure[member(ref, name)] = built
            else:
                registry.tools[(built.resource_type, built.name)] = built


def _build_tool(
    name: ToolName,
    fn: Callable[..., object],
    types_by_class: dict[type, TypeRef],
    ref: AdapterRef,
) -> Tool:
    """Derive one tool's schemas and the resource type it declares, if any.

    A tool with no resource parameter is pure: ``resource_type`` is None, and it is
    published under its own member ref rather than scoped to a type.

    Raises:
        AdapterError: it names a resource that is not declared here.
        ExtError: a schema cannot be derived.
    """
    holder, args, result = tool_schemas(fn)
    type_ref = types_by_class.get(holder) if isinstance(holder, type) else None
    if holder is not None and type_ref is None:
        raise AdapterError(f"{ref}: tool {name!r} takes {holder!r}, which is not a resource here")
    summary = (fn.__doc__ or "").strip().splitlines()
    return Tool(
        name=name,
        resource_type=type_ref,
        args=args,
        result=result,
        run=fn,
        description=summary[0] if summary else "",
    )
