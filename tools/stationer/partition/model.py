# Copyright (c) 2026 Project contributors.
"""Generic tool and component model for partitioned builds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolSpec:
    """One invocable tool exposed by a component.

    Attributes:
        name: Command name accepted by the launcher.
        entrypoint: Invocation target resolved by the launcher.
        component: Component that provides the tool.

    """

    name: str
    entrypoint: str
    component: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ComponentSpec:
    """One logical filesystem component and its dependency edges.

    Attributes:
        name: Stable logical component identifier.
        dependencies: Component names required transitively.
        tools: Invocable tools declared by the component.

    """

    name: str
    dependencies: tuple[str, ...] = ()
    tools: tuple[ToolSpec, ...] = ()


def resolve_components(
    *,
    requested: tuple[str, ...],
    components: Mapping[str, ComponentSpec],
) -> tuple[ComponentSpec, ...]:
    """Resolve component or tool names to a closed component set.

    Args:
        requested: Component or tool names to resolve.
        components: Available components keyed by name.

    Returns:
        Selected components closed over ``dependencies``, in
        ``components`` order.

    Raises:
        ValueError: A name matches no component or tool.

    """
    providers: dict[str, str] = {
        tool.name: spec.name
        for spec in components.values()
        for tool in spec.tools
    }
    selected: set[str] = set()
    for name in requested:
        component: str | None = (
            name if name in components else providers.get(name)
        )
        if component is None:
            msg: str = f"unknown component or tool: {name}"
            raise ValueError(msg)
        selected.add(component)
    pending: list[str] = list(selected)
    while pending:
        current: str = pending.pop()
        for dependency in components[current].dependencies:
            if dependency not in components:
                msg = (
                    f"component {current} requires unknown component:"
                    f" {dependency}"
                )
                raise ValueError(msg)
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return tuple(spec for name, spec in components.items() if name in selected)
