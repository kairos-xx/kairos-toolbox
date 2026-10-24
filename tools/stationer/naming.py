# Copyright (c) 2026 Project contributors.
"""Release asset and tag naming for kairos-toolbox projects."""

from __future__ import annotations

from typing import Final, final


@final
class Constants:
    """Group module-level constants.

    Attributes:
        checksum_name: Grouped module constant.

    """

    checksum_name: Final[str] = "SHA256SUMS.txt"


def asset_name(
    *,
    project: str,
    version: str,
    tag: str,
    suffix: str = "pyz",
) -> str:
    """Return the release-asset file name.

    Args:
        project: Project name.
        version: Project version.
        tag: Platform tag.
        suffix: File extension without the dot.

    Returns:
        ``<name>-<version>-<tag>.<suffix>``.

    """
    return f"{project}-{version}-{tag}.{suffix}"


def tag_name(*, project: str, version: str) -> str:
    """Return the release tag for one project version.

    Args:
        project: Project name.
        version: Project version.

    Returns:
        ``<project>/v<version>``.

    """
    return f"{project}/v{version}"


def parse_tag(*, tag: str) -> tuple[str, str] | None:
    """Split a ``<project>/v<version>`` tag into its parts.

    Args:
        tag: Release tag to parse.

    Returns:
        ``(project, version)``, or ``None`` for a malformed tag.

    """
    project, separator, version = tag.rpartition("/v")
    if not separator or not project or (not version):
        return None
    return (project, version)
