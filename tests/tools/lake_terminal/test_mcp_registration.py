# Copyright (c) 2026 Project contributors.
"""Tests for MCP client registration and the read-only server profile."""

from __future__ import annotations

from io import BytesIO, StringIO
from json import dumps, loads
from pathlib import Path
from subprocess import CompletedProcess
from typing import Final, cast, final

import pytest

from tools.lake_terminal.lake_terminal import cli as cli_module
from tools.lake_terminal.lake_terminal import mcp_registration as registration
from tools.lake_terminal.lake_terminal.cli import Cli
from tools.lake_terminal.lake_terminal.core import (
    LakeTerminal,
    SessionRepository,
)
from tools.lake_terminal.lake_terminal.mcp_registration import McpRegistration
from tools.lake_terminal.lake_terminal.mcp_server import LakeMcpServer

from .helpers import check, expect

type JsonObject = dict[str, object]


@final
class Constants:
    """Group module-level constants.

    Attributes:
        launcher: Grouped module constant.

    """

    launcher: Final[Path] = Path("/opt/bin/lake")


def frame(*, payload: JsonObject) -> bytes:
    """Encode one MCP request in Content-Length framing.

    Args:
        payload: JSON-RPC request object.

    Returns:
        Framed request bytes.

    """
    body: bytes = dumps(obj=payload).encode(encoding="utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def responses(*, output: bytes) -> list[JsonObject]:
    """Decode every framed response from the server output.

    Args:
        output: Raw server output bytes.

    Returns:
        Parsed response objects.

    """
    results: list[JsonObject] = []
    stream: BytesIO = BytesIO(initial_bytes=output)
    while header := stream.readline():
        length: int = int(header.decode(encoding="utf-8").partition(":")[2])
        _ = stream.readline()
        results.append(
            cast(typ="JsonObject", val=loads(s=stream.read(length))),
        )
    return results


def tool_call(
    *,
    identifier: int,
    name: str,
    arguments: JsonObject,
) -> JsonObject:
    """Build a tools/call request object.

    Args:
        identifier: Request identifier.
        name: Tool name.
        arguments: Tool arguments.

    Returns:
        JSON-RPC request object.

    """
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _no_cli(**_: object) -> None:
    """Report that the ``claude`` executable is not installed.

    Args:
        **_: Ignored lookup arguments.
        _: Test parameter.

    """
    return


def _fake_cli(**_: object) -> str:
    """Report an installed ``claude`` executable.

    Args:
        **_: Ignored lookup arguments.
        _: Test parameter.

    Returns:
        Fake executable path.

    """
    return "/usr/local/bin/claude"


def _desktop(*, home: Path) -> Path:
    """Create the directory of an installed Claude Desktop.

    Args:
        home: Home directory.

    Returns:
        The Claude Desktop configuration path.

    """
    path: Path = McpRegistration.claude_desktop_path(home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def without_claude_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force direct file edits instead of the real ``claude`` CLI.

    Args:
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setattr(target=registration, name="which", value=_no_cli)


@pytest.mark.parametrize(
    ("system_name", "expected"),
    [
        (
            "Darwin",
            "Library/Application Support/Claude/claude_desktop_config.json",
        ),
        ("Linux", ".config/Claude/claude_desktop_config.json"),
        ("Windows", "AppData/Roaming/Claude/claude_desktop_config.json"),
    ],
)
def test_claude_desktop_config_path_per_platform(
    monkeypatch: pytest.MonkeyPatch,
    system_name: str,
    expected: str,
) -> None:
    """Locate Claude Desktop's configuration on each operating system.

    Args:
        monkeypatch: Monkeypatch.
        system_name: Value reported by platform.system().
        expected: Path below the home directory.

    """
    monkeypatch.setattr(
        target=registration,
        name="system",
        value=lambda: system_name,
    )
    monkeypatch.delenv(name="APPDATA", raising=False)
    home: Path = Path("/home/u")
    expect(
        actual=McpRegistration.claude_desktop_path(home=home),
        expected=home / expected,
    )


def test_install_registers_in_claude_desktop_and_claude_code(
    tmp_path: Path,
) -> None:
    """Register the server in both Claude clients without losing config.

    Args:
        tmp_path: Tmp path.

    """
    desktop: Path = _desktop(home=tmp_path)
    _ = desktop.write_text(
        data=dumps(obj={"mcpServers": {"other": {"command": "x"}}}),
        encoding="utf-8",
    )
    written: tuple[Path, ...] = McpRegistration.install(
        home=tmp_path,
        launcher=Constants.launcher,
    )
    expect(actual=len(written), expected=2)
    servers: dict[str, dict[str, object]] = loads(
        s=desktop.read_text(encoding="utf-8"),
    )["mcpServers"]
    expect(actual=servers["other"], expected={"command": "x"})
    expect(
        actual=servers["lake-terminal"],
        expected={
            "command": str(object=Constants.launcher),
            "args": ["--mcp"],
        },
    )
    code: dict[str, dict[str, object]] = loads(
        s=McpRegistration.claude_code_path(home=tmp_path).read_text(
            encoding="utf-8",
        ),
    )["mcpServers"]
    expect(
        actual=code["lake-terminal"]["command"],
        expected=str(object=Constants.launcher),
    )
    check(
        condition=McpRegistration.installed(home=tmp_path),
        reason="registration detected",
    )


def test_install_is_idempotent_and_uninstall_removes_only_ours(
    tmp_path: Path,
) -> None:
    """Re-register cleanly and remove only the Lake entry.

    Args:
        tmp_path: Tmp path.

    """
    desktop: Path = _desktop(home=tmp_path)
    _ = desktop.write_text(
        data=dumps(obj={"mcpServers": {"other": {"command": "x"}}}),
        encoding="utf-8",
    )
    for _attempt in range(2):
        _ = McpRegistration.install(home=tmp_path, launcher=Constants.launcher)
    McpRegistration.uninstall(home=tmp_path)
    remaining: dict[str, object] = loads(
        s=desktop.read_text(encoding="utf-8"),
    )["mcpServers"]
    expect(actual=remaining, expected={"other": {"command": "x"}})
    check(
        condition=not McpRegistration.installed(home=tmp_path),
        reason="registration removed",
    )


def test_install_read_only_profile_adds_flag(tmp_path: Path) -> None:
    """Register the read-only tool profile on request.

    Args:
        tmp_path: Tmp path.

    """
    _ = _desktop(home=tmp_path)
    _ = McpRegistration.install(
        home=tmp_path,
        launcher=Constants.launcher,
        read_only=True,
    )
    entry: dict[str, object] = loads(
        s=McpRegistration.claude_desktop_path(home=tmp_path).read_text(
            encoding="utf-8",
        ),
    )["mcpServers"]["lake-terminal"]
    expect(actual=entry["args"], expected=["--mcp", "--read-only"])


def test_install_uses_claude_cli_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegate ~/.claude.json edits to the CLI that owns that file.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    _ = _desktop(home=tmp_path)
    calls: list[tuple[str, ...]] = []

    def _run(*, args: tuple[str, ...], **_: object) -> CompletedProcess[str]:
        """Return run.

        Args:
            args: Args.
            _: _.

        Returns:
            The resulting value.

        """
        calls.append(args)
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(target=registration, name="which", value=_fake_cli)
    monkeypatch.setattr(target=registration, name="run", value=_run)
    written: tuple[Path, ...] = McpRegistration.install(
        home=tmp_path,
        launcher=Constants.launcher,
    )
    expect(
        actual=written,
        expected=(McpRegistration.claude_desktop_path(home=tmp_path),),
    )
    expect(
        actual=calls[0],
        expected=(
            "/usr/local/bin/claude",
            "mcp",
            "add-json",
            "--scope",
            "user",
            "lake-terminal",
            dumps(
                obj={
                    "type": "stdio",
                    "command": str(object=Constants.launcher),
                    "args": ["--mcp"],
                },
                separators=(",", ":"),
            ),
        ),
    )
    check(
        condition=not McpRegistration.claude_code_path(home=tmp_path).exists(),
        reason="~/.claude.json untouched",
    )


def _read_only_exchange(
    *,
    tmp_path: Path,
    requests: list[JsonObject],
) -> list[JsonObject]:
    """Serve requests against a read-only MCP server.

    Args:
        tmp_path: Tmp path.
        requests: JSON-RPC requests.

    Returns:
        Response objects.

    """
    output: BytesIO = BytesIO()
    server: LakeMcpServer = LakeMcpServer(
        service=LakeTerminal(repository=SessionRepository(home=tmp_path)),
        reader=BytesIO(
            initial_bytes=b"".join(frame(payload=item) for item in requests),
        ),
        writer=output,
        read_only=True,
    )
    expect(actual=server.serve(), expected=0)
    return responses(output=output.getvalue())


def test_read_only_server_lists_only_inspection_tools(tmp_path: Path) -> None:
    """Hide every mutating tool from read-only clients.

    Args:
        tmp_path: Tmp path.

    """
    response: JsonObject = _read_only_exchange(
        tmp_path=tmp_path,
        requests=[{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}],
    )[0]
    tools: list[JsonObject] = cast(
        typ="list[JsonObject]",
        val=cast(typ="JsonObject", val=response["result"])["tools"],
    )
    expect(
        actual=sorted(str(object=tool["name"]) for tool in tools),
        expected=[
            "lake_commands",
            "lake_events",
            "lake_output",
            "lake_sessions",
        ],
    )


@pytest.mark.parametrize(
    "tool",
    ["lake_exec", "lake_send", "lake_new", "lake_kill"],
)
def test_read_only_server_rejects_mutating_calls(
    tmp_path: Path,
    tool: str,
) -> None:
    """Refuse mutating tool calls even when a client guesses their names.

    Args:
        tmp_path: Tmp path.
        tool: Mutating tool name.

    """
    result: JsonObject = cast(
        typ="JsonObject",
        val=_read_only_exchange(
            tmp_path=tmp_path,
            requests=[
                tool_call(
                    identifier=2,
                    name=tool,
                    arguments={"command": "echo hi"},
                ),
            ],
        )[0]["result"],
    )
    expect(actual=result["isError"], expected=True)
    check(
        condition="read-only" in dumps(obj=result["content"]),
        reason="explains the read-only profile",
    )


def test_install_skips_claude_desktop_when_not_installed(
    tmp_path: Path,
) -> None:
    """Never create Claude Desktop's directory on machines without it.

    Args:
        tmp_path: Tmp path.

    """
    written: tuple[Path, ...] = McpRegistration.install(
        home=tmp_path,
        launcher=Constants.launcher,
    )
    expect(
        actual=written,
        expected=(McpRegistration.claude_code_path(home=tmp_path),),
    )
    check(
        condition=not McpRegistration.claude_desktop_path(
            home=tmp_path,
        ).parent.exists(),
        reason="desktop directory not created",
    )


def test_doctor_reports_agent_identity_and_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Show what Lake resolved for this process and what is registered.

    Args:
        tmp_path: Tmp path.
        monkeypatch: Monkeypatch.

    """
    monkeypatch.setenv(name="HOME", value=str(object=tmp_path))
    monkeypatch.setenv(name="CLAUDE_CODE_SESSION_ID", value="cc-42")
    monkeypatch.setenv(name="CLAUDE_CODE_ENTRYPOINT", value="claude-desktop")
    printed: StringIO = StringIO()
    monkeypatch.setattr(target=cli_module, name="stdout", value=printed)
    cli: Cli = Cli(
        service=LakeTerminal(repository=SessionRepository(home=tmp_path)),
    )
    expect(actual=cli.run(arguments=("--doctor",)), expected=0)
    report: str = printed.getvalue()
    check(
        condition="claude-code · cc-42 · CLAUDE_CODE_SESSION_ID" in report,
        reason="agent identity row",
    )
    check(condition="Claude MCP server" in report, reason="mcp row")
    check(condition="missing" in report, reason="unregistered flagged")
