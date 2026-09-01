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
type LocalName = str  # a skill's own name for a call it makes, e.g. ``fetch``
type ArgName = str  # key in a call's args mapping
type SkillRef = str  # ``<namespace>/<name>``, or a path
type SkillName = str  # what an install calls one instantiation of a skill

# endregion


class ResourceError(RuntimeError):
    """A resource is missing, mis-typed, or a tool call failed."""


@dataclass(frozen=True, slots=True)
class Instance:
    """One configured resource: the author's handle, plus what the harness calls it."""

    name: InstanceName
    type: TypeRef  # the resource type it was built from
    handle: object  # the adapter's own dataclass instance, passed to its tools


@dataclass(frozen=True, slots=True)
class Binding:
    """What an install says fills one resource a skill declares: an instance, and which
    of its tools the skill's own call names reach.

    The skill file names calls in its own vocabulary, so nothing an adapter owns is
    written in it. This is where the two vocabularies meet, and it is the complete grant:
    a tool the skill does not map is not reachable through this resource. ``Bound`` is
    this with the instance resolved.
    """

    instance: InstanceName
    tools: dict[LocalName, ToolName] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Bound:
    """One resource a run holds: the instance, and this skill's names for its tools.

    The map is the complete list of what the skill may do to the instance — a call it
    does not name is not reachable. It belongs to the run rather than to the instance,
    since two skills bound to one mailbox name its tools however they like.
    """

    instance: Instance
    tools: dict[LocalName, ToolName] = field(default_factory=dict)

    def tool(self, call: LocalName) -> ToolName:
        """The adapter tool one local call name was mapped to.

        Raises:
            ResourceError: the binding does not map that name.
        """
        found = self.tools.get(call)
        if found is None:
            raise ResourceError(
                f"{self.instance.name} is not bound to a tool for {call!r}; "
                f"this skill maps {sorted(self.tools)}"
            )
        return found


@dataclass(slots=True)
class Registry:
    """What an install knows: resource types, their tools, instances, and skills.

    Populated by lila.adapters and lila.skills from what is installed, then read by the
    run loop, the static check, and the CLI.
    """

    types: dict[TypeRef, type] = field(default_factory=dict)
    tools: dict[tuple[TypeRef, ToolName], Tool] = field(default_factory=dict)
    # Pure tools declare no resource, so nothing scopes them but the adapter that
    # published them: they are addressed by full member ref, like a type or a skill.
    pure: dict[TypeRef, Tool] = field(default_factory=dict)
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

    def pure_tool(self, ref: TypeRef) -> Tool:
        """The pure tool published under a full member ref.

        Raises:
            ResourceError: nothing is installed under that ref.
        """
        found = self.pure.get(ref)
        if found is None:
            raise ResourceError(f"no pure tool {ref!r}; installed: {sorted(self.pure)}")
        return found

    def tools_of(self, type_ref: TypeRef) -> dict[ToolName, Tool]:
        """Every tool defined over one resource type."""
        return {name: tool for (ref, name), tool in self.tools.items() if ref == type_ref}
