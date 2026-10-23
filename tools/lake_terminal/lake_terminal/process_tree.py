# Copyright (c) 2026 Project contributors.
"""Process-tree inspection and command correlation."""

from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError, loads
from os import name as os_name
from typing import TYPE_CHECKING, cast

from .identity import ProcessIdentity
from .process import run

if TYPE_CHECKING:
    from subprocess import CompletedProcess
type ProcessValue = None | bool | int | float | str
type ProcessRow = dict[str, ProcessValue]
type ProcessPayload = ProcessRow | list[ProcessRow]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessNode:
    """One process observed below a managed session.

    Attributes:
        pid: Pid.
        parent_pid: Parent pid.
        command: Command.
        started_token: Started token.

    """

    pid: int
    parent_pid: int
    command: str
    started_token: str = ""


class ProcessInspector:
    """Inspect descendants without assuming the immediate child is workload."""

    @classmethod
    def descendants(cls, *, root_pid: int) -> tuple[ProcessNode, ...]:
        """Return all descendants of a root process.

        Args:
            root_pid: Root pid.

        Returns:
            The resulting value.

        """
        if root_pid <= 0:
            return ()
        nodes: tuple[ProcessNode, ...] = (
            cls.windows_nodes() if os_name == "nt" else cls.unix_nodes()
        )
        children: dict[int, list[ProcessNode]] = {}
        for node in nodes:
            children.setdefault(node.parent_pid, []).append(node)
        queue: list[int] = [root_pid]
        result: list[ProcessNode] = []
        while queue:
            parent: int = queue.pop(0)
            for child in children.get(parent, []):
                result.append(child)
                queue.append(child.pid)
        return tuple(result)

    @classmethod
    def workload(cls, *, root_pid: int) -> ProcessNode | None:
        """Return the deepest currently observed descendant.

        Args:
            root_pid: Root pid.

        Returns:
            The resulting value.

        """
        descendants: tuple[ProcessNode, ...] = cls.descendants(
            root_pid=root_pid,
        )
        return descendants[-1] if descendants else None

    @classmethod
    def windows_nodes(cls) -> tuple[ProcessNode, ...]:
        """Read Windows processes using PowerShell JSON.

        Returns:
            The resulting value.

        """
        script: str = (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId,Name | "
            "ConvertTo-Json -Compress"
        )
        try:
            result: CompletedProcess[str] = run(
                args=(
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    script,
                ),
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return ()
        if result.returncode != 0 or not result.stdout:
            return ()
        try:
            raw: object = loads(s=result.stdout)
        except JSONDecodeError:
            return ()
        payload: ProcessPayload = cast(typ="ProcessPayload", val=raw)
        rows: list[ProcessRow] = (
            payload if isinstance(payload, list) else [payload]
        )
        nodes: list[ProcessNode] = []
        for row in rows:
            pid: int = cls.integer(
                value=row.get("ProcessId"),
            )
            if pid <= 0:
                continue
            parent_pid: int = cls.integer(
                value=row.get("ParentProcessId"),
            )
            command: str = cls.text(
                value=row.get("Name"),
            )
            nodes.append(
                ProcessNode(
                    pid=pid,
                    parent_pid=parent_pid,
                    command=command,
                ),
            )
        return tuple(nodes)

    @staticmethod
    def unix_nodes() -> tuple[ProcessNode, ...]:
        """Read a portable Unix process table.

        Returns:
            The resulting value.

        """
        try:
            result: CompletedProcess[str] = run(
                args=("ps", "-eo", "pid=,ppid=,comm="),
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return ()
        nodes: list[ProcessNode] = []
        expected_parts: int = 3
        for line in result.stdout.splitlines():
            parts: list[str] = line.strip().split(maxsplit=2)
            if (
                len(parts) != expected_parts
                or not parts[0].isdigit()
                or not parts[1].isdigit()
            ):
                continue
            pid: int = int(parts[0])
            try:
                identity: ProcessIdentity = ProcessIdentity.capture(
                    pid=pid,
                )
                token: str = identity.started_token
            except OSError:
                token = ""
            nodes.append(
                ProcessNode(
                    pid=pid,
                    parent_pid=int(parts[1]),
                    command=parts[2],
                    started_token=token,
                ),
            )
        return tuple(nodes)

    @staticmethod
    def integer(*, value: ProcessValue) -> int:
        """Convert a process-table value to an integer.

        Args:
            value: Process-table value.

        Returns:
            Integer representation, or zero for unsupported values.

        """
        if isinstance(value, bool) or value is None:
            return 0
        if isinstance(value, (float, str)):
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                return 0
        return 0

    @staticmethod
    def text(*, value: ProcessValue) -> str:
        """Convert a process-table value to text.

        Args:
            value: Process-table value.

        Returns:
            Text representation.

        """
        if value is None:
            return ""
        return str(object=value)
