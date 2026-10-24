# Copyright (c) 2026 Project contributors.
"""Build-target identity for partitioned releases."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildTarget:
    """Target platform description for one build.

    Attributes:
        os: Operating system family.
        arch: CPU architecture.
        abi: ABI or libc flavor (``glibc``, ``musl``, ``native``).

    """

    os: str
    arch: str
    abi: str
