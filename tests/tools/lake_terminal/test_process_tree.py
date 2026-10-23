# Copyright (c) 2026 Project contributors.
"""Tests for portable process-tree inspection."""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import TYPE_CHECKING

from tools.lake_terminal.lake_terminal import process_tree
from tools.lake_terminal.lake_terminal.process_tree import (
    ProcessInspector,
    ProcessNode,
)

from .helpers import expect

if TYPE_CHECKING:
    import pytest


def test_process_tree_descendants_and_workload(
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Traverse descendants and select the deepest workload.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    nodes: tuple[ProcessNode, ...] = (
        ProcessNode(pid=2, parent_pid=1, command="shell"),
        ProcessNode(pid=3, parent_pid=2, command="worker"),
        ProcessNode(pid=4, parent_pid=1, command="peer"),
    )

    def fake_unix_nodes() -> tuple[ProcessNode, ...]:
        """Return the process nodes used by the traversal test.

        Returns:
            Process nodes used by the traversal test.

        """
        return nodes

    monkeypatch.setattr(
        target=ProcessInspector,
        name="unix_nodes",
        value=staticmethod(fake_unix_nodes),
    )
    expect(actual=ProcessInspector.descendants(root_pid=0), expected=())
    descendants: tuple[ProcessNode, ...] = ProcessInspector.descendants(
        root_pid=1,
    )
    expect(actual=tuple(node.pid for node in descendants), expected=(2, 4, 3))
    expect(actual=ProcessInspector.workload(root_pid=1), expected=nodes[1])
    expect(actual=ProcessInspector.workload(root_pid=99), expected=None)


def test_process_tree_windows_payloads(
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse valid and invalid PowerShell process payloads.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    outputs: list[CompletedProcess[str] | OSError] = [
        CompletedProcess(
            args=("powershell.exe",),
            returncode=0,
            stdout=(
                '[{"ProcessId":"2","ParentProcessId":1,"Name":"pwsh"},'
                '{"ProcessId":0,"ParentProcessId":1,"Name":"skip"}]'
            ),
            stderr="",
        ),
        CompletedProcess(
            args=("powershell.exe",),
            returncode=0,
            stdout=(
                '{ "ProcessId": "3", "ParentProcessId": "2", "Name": null }'
            ),
            stderr="",
        ),
        CompletedProcess(
            args=("powershell.exe",),
            returncode=1,
            stdout="",
            stderr="failed",
        ),
        CompletedProcess(
            args=("powershell.exe",),
            returncode=0,
            stdout="not-json",
            stderr="",
        ),
        OSError("missing"),
    ]

    def fake_run(
        *,
        args: tuple[str, ...],
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> CompletedProcess[str]:
        """Return the next simulated PowerShell process result.

        Args:
            args: Command arguments supplied to the process runner.
            check: Whether nonzero exit status should raise.
            capture_output: Whether standard streams should be captured.
            text: Whether standard streams should be decoded as text.

        Returns:
            Next simulated PowerShell process result.

        """
        del args, check, capture_output, text
        result: CompletedProcess[str] | OSError = outputs.pop(0)
        if isinstance(result, OSError):
            raise result
        return result

    monkeypatch.setattr(target=process_tree, name="run", value=fake_run)
    expect(
        actual=ProcessInspector.windows_nodes(),
        expected=(ProcessNode(pid=2, parent_pid=0, command="pwsh"),),
    )
    expect(
        actual=ProcessInspector.windows_nodes(),
        expected=(ProcessNode(pid=3, parent_pid=2, command=""),),
    )
    expect(actual=ProcessInspector.windows_nodes(), expected=())
    expect(actual=ProcessInspector.windows_nodes(), expected=())
    expect(actual=ProcessInspector.windows_nodes(), expected=())


def test_process_tree_unix_payload_and_converters(
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse Unix rows and exercise defensive scalar conversion.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """

    def fake_run(
        *,
        args: tuple[str, ...],
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> CompletedProcess[str]:
        """Return the simulated Unix process-list result.

        Args:
            args: Command arguments supplied to the process runner.
            check: Whether nonzero exit status should raise.
            capture_output: Whether standard streams should be captured.
            text: Whether standard streams should be decoded as text.

        Returns:
            Simulated Unix process-list result.

        """
        del args, check, capture_output, text
        return CompletedProcess(
            args=("ps",),
            returncode=0,
            stdout="2 1 shell\ninvalid\n3 x bad\n4 2 worker\n",
            stderr="",
        )

    def fake_capture(*, pid: int) -> process_tree.ProcessIdentity:
        """Return a simulated process identity.

        Args:
            pid: Process identifier to inspect.

        Returns:
            Simulated process identity.

        Raises:
            OSError: If the simulated process vanished.

        """
        pid_gone: int = 4
        if pid == pid_gone:
            msg: str = "gone"
            raise OSError(msg)
        return process_tree.ProcessIdentity(
            pid=pid,
            started_token=f"token-{pid}",
        )

    monkeypatch.setattr(target=process_tree, name="run", value=fake_run)
    monkeypatch.setattr(
        target=process_tree.ProcessIdentity,
        name="capture",
        value=staticmethod(fake_capture),
    )
    expect(
        actual=ProcessInspector.unix_nodes(),
        expected=(
            ProcessNode(
                pid=2,
                parent_pid=1,
                command="shell",
                started_token="token-" + str(object=2),
            ),
            ProcessNode(pid=4, parent_pid=2, command="worker"),
        ),
    )
    expect(actual=ProcessInspector.integer(value=None), expected=0)
    expect(actual=ProcessInspector.integer(value=True), expected=0)
    expect(actual=ProcessInspector.integer(value="12"), expected=12)
    expect(actual=ProcessInspector.integer(value="bad"), expected=0)
    expect(actual=ProcessInspector.integer(value=float("inf")), expected=0)
    expect(actual=ProcessInspector.integer(value=7), expected=0)
    expect(actual=ProcessInspector.text(value=None), expected="")
    expect(actual=ProcessInspector.text(value=7), expected="7")
