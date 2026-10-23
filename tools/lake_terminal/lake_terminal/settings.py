# Copyright (c) 2026 Project contributors.
"""Report every effective Lake Terminal setting as one JSON document."""

from __future__ import annotations

from dataclasses import asdict
from os import environ
from typing import TYPE_CHECKING, Final

from .installer import Layout, manifest_layout, read_manifest
from .privacy import PrivacyPolicy
from .product import Product, state_directory
from .update import UpdatePolicy, UpdateState, is_newer

if TYPE_CHECKING:
    from pathlib import Path

    from .installer import ManifestData
type Section = dict[str, object]


class SettingsConstants:
    """Group settings-report constants.

    Attributes:
        environment: Variables whose presence (never value) is reported.

    """

    environment: Final[tuple[str, ...]] = (
        "LAKE_DISABLE_UPDATE_CHECK",
        "LAKE_UPDATE_REPO",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "LAKE_DISABLE_AGENT",
    )


def _install_section(*, home: Path) -> Section | None:
    """Describe the recorded installation.

    Args:
        home: User home directory.

    Returns:
        Install facts, or None when nothing is installed.

    """
    layout: Layout | None = manifest_layout(home=home)
    if layout is None:
        return None
    data: ManifestData = read_manifest(layout=layout)
    return {
        "mode": layout.mode,
        "alias": layout.alias,
        "launcher": str(object=layout.launcher),
        "bin_dir": str(object=layout.bin_dir),
        "lib_dir": str(object=layout.lib_dir),
        "payload": layout.payload,
        "source": str(object=layout.source_root),
        "python": layout.python,
        "version": data.get("version", ""),
        "components": list(data.get("components", [])),
        "installed_at": data.get("installed_at", ""),
    }


def _updates_section(*, home: Path) -> Section:
    """Describe the update policy and cached check state.

    Args:
        home: User home directory.

    Returns:
        Update settings.

    """
    policy: UpdatePolicy = UpdatePolicy.load(home=home)
    state: UpdateState = UpdateState.load(home=home)
    directory: Path = state_directory(home=home)
    return {
        "enabled": policy.enabled,
        "check": policy.check,
        "channel": policy.channel,
        "repository": policy.repository,
        "repository_from_env": policy.repository_from_env,
        "tag_prefix": policy.tag_prefix,
        "timeout_seconds": policy.timeout_seconds,
        "last_check": state.checked_at or None,
        "latest": state.latest or None,
        "release_url": state.release_url or None,
        "update_available": is_newer(
            candidate=state.latest,
            current=Product.version,
        ),
        "config_file": str(object=directory / "update.json"),
        "state_file": str(object=directory / "update-state.json"),
    }


def settings_document(*, home: Path) -> Section:
    """Build the ordered settings report for ``lake --settings``.

    Args:
        home: User home directory.

    Returns:
        JSON-serialisable settings; secrets are never included.

    """
    directory: Path = state_directory(home=home)
    policy: PrivacyPolicy = PrivacyPolicy.load(home=home)
    privacy: Section = {
        **asdict(obj=policy),
        "redact_patterns": list(policy.redact_patterns),
        "config_file": str(object=directory / "privacy.json"),
    }
    return {
        "version": Product.version,
        "product": {"name": Product.name, "command": Product.command},
        "install": _install_section(home=home),
        "updates": _updates_section(home=home),
        "privacy": privacy,
        "paths": {
            "state_directory": str(object=directory),
            "database": str(object=directory / Product.database_name),
        },
        "environment": {
            name: bool(environ.get(name))
            for name in SettingsConstants.environment
        },
    }
