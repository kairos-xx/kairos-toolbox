# Copyright (c) 2026 Project contributors.
"""Tests for release asset and tag naming."""

from __future__ import annotations

from stationer.naming import (
    CHECKSUM_NAME,
    asset_name,
    parse_tag,
    tag_name,
)

from tests.tools.stationer.helpers import check, expect


def test_checksum_name_is_stable() -> None:
    """The manifest name district's updater reads stays fixed."""
    expect(actual=CHECKSUM_NAME, expected="SHA256SUMS.txt")


def test_asset_name_default_and_suffix() -> None:
    """Asset names follow ``<name>-<version>-<tag>.<suffix>``."""
    expect(
        actual=asset_name(project="maire", version="1.2.3", tag="linux-x86_64"),
        expected="maire-1.2.3-linux-x86_64.pyz",
    )
    expect(
        actual=asset_name(
            project="maire",
            version="1.2.3",
            tag="windows-amd64",
            suffix="zip",
        ),
        expected="maire-1.2.3-windows-amd64.zip",
    )


def test_tag_name_and_parse_tag() -> None:
    """Tags are ``<project>/v<version>`` and parse round-trip."""
    expect(
        actual=tag_name(project="maire", version="2.0.0"),
        expected="maire/v2.0.0",
    )
    expect(
        actual=parse_tag(tag="kairos-toolbox/v1.4.2"),
        expected=("kairos-toolbox", "1.4.2"),
    )
    check(
        condition=parse_tag(tag="v1.0.0") is None,
        reason="Project-less tag parsed.",
    )
    check(
        condition=parse_tag(tag="maire") is None,
        reason="Separator-less tag parsed.",
    )
