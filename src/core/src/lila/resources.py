"""Resource instances and the registry a run resolves names against.

A resource holds config, credentials, and session lifecycle and has no operations —
those are tools (lila.ext). An instance never enters run memory or the record, so
credentials cannot reach a prompt. A graph names a resource (``inbox``); an install
binds that name to an instance (``gmail-personal``) of a declared type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path as FilePath

from lila.ext import Tool, ToolName, TypeRef

# region names

# The lower half of the name vocabulary; lila.executor re-exports these with its own.
type ResourceName = str  # what a graph calls a resource it needs, e.g. ``inbox``
type InstanceName = str  # a configured instance, e.g. ``gmail-personal``
type ArgName = str  # key in a call's args mapping
type SkillRef = str  # ``publisher/extension@version/member``, or a path

# endregion


class ResourceError(RuntimeError):
    """A resource is missing, mis-typed, or a tool call failed."""


@dataclass(frozen=True, slots=True)
class Instance:
    """One configured resource: the author's handle, plus what the harness calls it."""

    name: InstanceName
    type: TypeRef  # the resource type it was built from
    handle: object  # the extension's own dataclass instance, passed to its tools


@dataclass(slots=True)
class Registry:
    """What an install knows: resource types, their tools, instances, and skills.

    Populated by lila.extensions from installed extensions, then read by the run loop,
    the static check, and the CLI.
    """

    types: dict[TypeRef, type] = field(default_factory=dict)
    tools: dict[tuple[TypeRef, ToolName], Tool] = field(default_factory=dict)
    instances: dict[InstanceName, Instance] = field(default_factory=dict)
    skills: dict[SkillRef, FilePath] = field(default_factory=dict)

    def register(self, instance: Instance) -> None:
        """Add an instance under its own name, replacing any existing one."""
        self.instances[instance.name] = instance

    def instance(self, name: InstanceName) -> Instance:
        """The instance configured under a name.

        Raises:
            ResourceError: nothing is configured under that name.
        """
        found = self.instances.get(name)
        if found is None:
            raise ResourceError(f"no resource configured as {name!r}")
        return found

    def tool(self, type_ref: TypeRef, call: ToolName) -> Tool:
        """The tool a resource type defines under a name.

        Raises:
            ResourceError: that type has no such tool.
        """
        found = self.tools.get((type_ref, call))
        if found is None:
            known = sorted(name for ref, name in self.tools if ref == type_ref)
            raise ResourceError(f"{type_ref} has no tool {call!r}; it has {known}")
        return found

    def tools_of(self, type_ref: TypeRef) -> dict[ToolName, Tool]:
        """Every tool defined over one resource type."""
        return {name: tool for (ref, name), tool in self.tools.items() if ref == type_ref}

    def bind(
        self,
        declared: dict[ResourceName, TypeRef],
        bindings: dict[ResourceName, InstanceName],
    ) -> dict[ResourceName, Instance]:
        """Map a skill's declared resources to instances, refusing unbound or mis-typed.

        Raises:
            ResourceError: a name has no binding, names an unconfigured instance, or
                the instance is of a different type.
        """
        bound: dict[ResourceName, Instance] = {}
        for name, type_ref in declared.items():
            binding = bindings.get(name)
            if binding is None:
                raise ResourceError(f"resource {name!r} is unbound")
            found = self.instance(binding)
            if found.type != type_ref:
                raise ResourceError(
                    f"resource {name!r} wants {type_ref}, {binding!r} is {found.type}"
                )
            bound[name] = found
        return bound
