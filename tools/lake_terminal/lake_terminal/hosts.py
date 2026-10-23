# Copyright (c) 2026 Project contributors.
"""Configure and query local/remote Lake Terminal execution hosts."""

from __future__ import annotations

from dataclasses import dataclass
from json import dumps
from typing import TYPE_CHECKING

from .identity import IdentityStore
from .process import run
from .storage import LakeDatabase

if TYPE_CHECKING:
    from pathlib import Path
    from subprocess import CompletedProcess


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteHost:
    """One SSH-accessible Lake Terminal execution host.

    Attributes:
        name: Name.
        target: Target.
        id: Id.
        command: Command.
        enabled: Enabled.

    """

    name: str
    target: str
    id: str = ""
    command: str = "lake"
    enabled: bool = True


class HostRepository:
    """Persist remote-host definitions in the canonical SQLite database."""

    def __init__(self, *, home: Path) -> None:
        """Initialize the SQLite-backed host repository.

        Args:
            home: Home.

        """
        self._database: LakeDatabase = LakeDatabase(home=home)

    def read(self) -> tuple[RemoteHost, ...]:
        """Return configured remote hosts.

        Returns:
            The resulting value.

        """
        return tuple(
            RemoteHost(
                id=host_id,
                name=name,
                target=target,
                command=command,
                enabled=enabled,
            )
            for host_id, name, target, command, enabled in (
                self._database.host_details()
            )
        )

    def upsert(self, *, host: RemoteHost) -> None:
        """Create or replace one host definition.

        Args:
            host: Host.

        """
        existing: RemoteHost | None = next(
            (item for item in self.read() if item.name == host.name),
            None,
        )
        host_id: str = host.id or (
            existing.id if existing is not None else IdentityStore.new_id()
        )
        self._database.upsert_host(
            host_id=host_id,
            name=host.name,
            target=host.target,
            command=host.command,
            enabled=host.enabled,
        )

    def remove(self, *, name: str) -> None:
        """Remove one host definition by name.

        Args:
            name: Name.

        """
        self._database.remove_host(name=name)


class RemoteAggregator:
    """Query configured hosts using machine-readable Lake Terminal status."""

    def __init__(self, *, repository: HostRepository) -> None:
        """Store the host repository.

        Args:
            repository: Repository.

        """
        self._repository: HostRepository = repository

    def statuses(self) -> tuple[tuple[RemoteHost, str], ...]:
        """Return raw JSON status from every enabled remote host.

        Returns:
            The resulting value.

        """
        results: list[tuple[RemoteHost, str]] = []
        for host in self._repository.read():
            if not host.enabled:
                continue
            result: CompletedProcess[str] = run(
                args=(
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=3",
                    host.target,
                    host.command,
                    "--status",
                    "--json",
                    "--local-only",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                results.append((host, (result.stdout or "{}").strip()))
            else:
                message: str = (result.stderr or "unreachable").strip()
                results.append((host, dumps(obj={"error": message})))
        return tuple(results)
