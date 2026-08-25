"""Resources: named, capability-scoped objects injected into a node.

A resource never enters run memory or the record, so credentials cannot reach a prompt.
A graph declares slots (``inbox: mailbox@1``); an install binds each slot to an instance.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from lila.values import Json

# region names

# The lower half of the name vocabulary; lila.executor re-exports these with its own.
type SlotName = str  # a slot a graph declares, e.g. ``inbox``
type InterfaceName = str  # versioned capability, e.g. ``mailbox@1``
type BindingName = str  # a registered instance, e.g. ``gmail-personal``
type CallName = str  # an operation on an interface, e.g. ``list_messages``
type ArgName = str  # key in a call's args mapping

# endregion


class ResourceError(RuntimeError):
    """A resource is missing, mis-typed, or failed a call."""


@runtime_checkable
class Resource(Protocol):
    """A capability contract instance, e.g. some concrete ``mailbox@1``."""

    @property
    @abstractmethod
    def name(self) -> BindingName:
        """Binding name of this instance, e.g. ``gmail-personal``."""

    @property
    @abstractmethod
    def interface(self) -> InterfaceName:
        """Versioned interface it implements, e.g. ``mailbox@1``."""

    @abstractmethod
    def call(self, operation: CallName, args: dict[ArgName, Json]) -> dict[str, Json]:
        """Invoke one operation. Raises ResourceError when it fails."""


class ResourceRegistry:
    """Binding name -> instance, plus slot binding and typecheck."""

    def __init__(self, instances: dict[BindingName, Resource] | None = None) -> None:
        """Start from an optional binding name -> instance mapping."""
        self._instances: dict[BindingName, Resource] = dict(instances or {})

    def register(self, resource: Resource) -> None:
        """Add an instance under its own name, replacing any existing one."""
        self._instances[resource.name] = resource

    def get(self, binding: BindingName) -> Resource:
        """The instance registered under a binding name.

        Raises:
            ResourceError: nothing is bound under that name.
        """
        instance = self._instances.get(binding)
        if instance is None:
            raise ResourceError(f"no resource bound as {binding!r}")
        return instance

    def bind(
        self,
        requires: dict[SlotName, InterfaceName],
        bindings: dict[SlotName, BindingName],
    ) -> dict[SlotName, Resource]:
        """Map declared slots to instances, refusing an unbound or mis-typed slot.

        Raises:
            ResourceError: a slot has no binding, names an unregistered instance, or
                the instance implements a different interface.
        """
        bound: dict[SlotName, Resource] = {}
        for slot, interface in requires.items():
            binding = bindings.get(slot)
            if binding is None:
                raise ResourceError(f"slot {slot!r} is unbound")
            instance = self.get(binding)
            if instance.interface != interface:
                raise ResourceError(
                    f"slot {slot!r} wants {interface}, {binding!r} implements {instance.interface}"
                )
            bound[slot] = instance
        return bound
