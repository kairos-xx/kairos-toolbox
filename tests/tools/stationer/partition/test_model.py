# Copyright (c) 2026 Project contributors.
"""Tests for the generic partition tool model."""

from __future__ import annotations

from dataclasses import fields

import pytest
from stationer.partition.model import (
    ComponentSpec,
    ToolSpec,
    resolve_components,
)
from stationer.partition.target import BuildTarget

from tests.tools.stationer.helpers import check, expect

_COMPONENTS: dict[str, ComponentSpec] = {
    "core": ComponentSpec(
        name="core",
        tools=(
            ToolSpec(name="core", entrypoint="bin/core", component="core"),
        ),
    ),
    "lang": ComponentSpec(
        name="lang",
        dependencies=("core",),
        tools=(
            ToolSpec(name="lang", entrypoint="bin/lang", component="lang"),
        ),
    ),
    "app": ComponentSpec(
        name="app",
        dependencies=("lang",),
        tools=(ToolSpec(name="app", entrypoint="bin/app", component="app"),),
    ),
    "extra": ComponentSpec(name="extra"),
}


def test_resolve_components_includes_transitive_dependencies() -> None:
    """Close the selection over dependency edges in table order."""
    resolved: tuple[ComponentSpec, ...] = resolve_components(
        requested=("app",),
        components=_COMPONENTS,
    )
    expect(
        actual=tuple(spec.name for spec in resolved),
        expected=("core", "lang", "app"),
    )


def test_resolve_components_accepts_tool_names() -> None:
    """Resolve a tool name to the component that provides it."""
    resolved: tuple[ComponentSpec, ...] = resolve_components(
        requested=("lang",),
        components=_COMPONENTS,
    )
    names: tuple[str, ...] = tuple(spec.name for spec in resolved)
    check(
        condition="lang" in names and "core" in names,
        reason="Tool resolution missed the provider or its dependency.",
    )


def test_resolve_components_rejects_unknown_names() -> None:
    """Raise when a requested name matches no component or tool."""
    with pytest.raises(expected_exception=ValueError, match="unknown"):
        resolve_components(requested=("ghost",), components=_COMPONENTS)


def test_resolve_components_rejects_dangling_dependency() -> None:
    """Raise when a declared dependency is not a known component."""
    with pytest.raises(
        expected_exception=ValueError,
        match="unknown component",
    ):
        resolve_components(
            requested=("app",),
            components={
                "app": ComponentSpec(name="app", dependencies=("lost",)),
            },
        )


def test_build_target_is_frozen_identity() -> None:
    """Carry os/arch/abi as an immutable triple."""
    target: BuildTarget = BuildTarget(os="linux", arch="amd64", abi="glibc")
    expect(
        actual=(target.os, target.arch, target.abi),
        expected=("linux", "amd64", "glibc"),
    )
    check(
        condition=getattr(
            getattr(type(target), "__dataclass_params__", None),
            "frozen",
            False,
        )
        is True,
        reason="BuildTarget is not frozen.",
    )
    expect(actual=len(fields(target)), expected=3)
