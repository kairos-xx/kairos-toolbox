# Copyright (c) 2026 Project contributors.
"""Command-line interface for District agent execution observability."""

from __future__ import annotations

from dataclasses import replace
from importlib import import_module
from json import dumps, loads
from operator import attrgetter
from os import environ
from pathlib import Path
from re import fullmatch
from shlex import join
from shutil import which
from subprocess import CalledProcessError
from sys import argv, stderr, stdout
from typing import TYPE_CHECKING, Final, Protocol, TypeGuard, cast
from urllib.parse import urlencode

from .agent import AgentDetector
from .agent_identity import AgentIdentity
from .claude import ClaudeHooks
from .codex import CodexHooks
from .core import (
    District,
    ExecResult,
    SessionRepository,
    default_service,
)
from .cursor import CursorHooks
from .daemon import DistrictDaemon
from .devin import DevinHooks
from .events import (
    DistrictEvent,
    EventDetails,
    EventTypes,
)
from .exposure import (
    Exposure,
    ExposureConstants,
    ExposureError,
    load_exposure,
)
from .history import History
from .hosts import (
    HostRepository,
    RemoteAggregator,
    RemoteHost,
)
from .identity import IdentityStore
from .integration import IntegrationHarness, IntegrationResult
from .ipc import EventSubscriber
from .lifecycle import CommandLifecycle
from .mcp_registration import McpRegistration
from .mcp_server import DistrictMcpServer
from .monitoring import Monitor, SessionSnapshot
from .pairing import PairingUnavailableError
from .privacy import PrivacyPolicy
from .product import Product, state_directory
from .recorder import EventRecorder
from .runtime import (
    Capabilities,
    RuntimePlatform,
)
from .selftest import self_test
from .startup import StartupState, WebStartup
from .storage import DistrictDatabase
from .tui import WatchTui
from .web import WebConstants, WebDashboard

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from .models import SessionEntry
type StatusValue = str | int | bool
type StatusRow = dict[str, StatusValue]
type JsonValue = (
    bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
    | None
)


class HookRunner(Protocol):
    """Describe one provider hook runner callable."""

    def __call__(self, *, home: Path) -> int:
        """Run one provider hook invocation.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        ...


def _is_json_object(
    value: JsonValue,
    /,
) -> TypeGuard[dict[str, JsonValue]]:
    """Return whether a JSON value is an object.

    Args:
        value: JSON value.

    Returns:
        Whether the value is a JSON object.

    """
    return isinstance(value, dict)


def status_value(*, value: JsonValue) -> StatusValue | None:
    """Narrow a JSON value to a supported status value.

    Args:
        value: JSON value.

    Returns:
        Supported status value when applicable.

    """
    if isinstance(value, str | int | bool):
        return value
    return None


class Constants:
    """Group immutable command-line constants.

    Attributes:
        program: Program.
        help_text: Help text.
        host_add_min_args: Minimum host-add arguments.
        host_add_max_args: Maximum host-add arguments.
        host_remove_args: Host-remove arguments.
        lifecycle_start_min_args: Minimum lifecycle-start arguments.
        lifecycle_finish_args: Lifecycle-finish arguments.
        agent_state_args: Agent-state arguments.
        cleanup_args: Cleanup arguments.
        handle_uri_args: ``--handle-uri`` arguments.
        maximum_port: Maximum TCP port.
        update_notice_options: Read-only commands allowed to show notices.
        update_silent_commands: Commands that never trigger update checks.
        session_name_markers: Provider session-name environment markers.

    """

    program: Final[str] = "district"
    host_add_min_args: Final[int] = 2
    host_add_max_args: Final[int] = 3
    host_remove_args: Final[int] = 1
    lifecycle_start_min_args: Final[int] = 2
    lifecycle_finish_args: Final[int] = 2
    agent_state_args: Final[int] = 1
    cleanup_args: Final[int] = 2
    handle_uri_args: Final[int] = 2
    maximum_port: Final[int] = 65_535
    update_notice_options: Final[frozenset[str]] = frozenset(
        {"--list", "--watch", "--view", "--doctor"},
    )
    update_silent_commands: Final[frozenset[str]] = frozenset(
        {"test", "explain"},
    )
    session_name_markers: Final[tuple[str, ...]] = (
        "DEVIN_SESSION_ID",
        "CURSOR_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CODEX_THREAD_ID",
    )
    help_text: Final[str] = """\
district — cross-platform agent execution observability and control

QUICK START
  district --status
  district --watch
  district --web
  district --doctor --full
  district explain SESSION_ID
  district test integration synthetic [PROVIDER|all]
  district test integration concurrent
  district test integration real PROVIDER

PURPOSE
  district captures and attributes shell/process activity from human terminals
  and supported coding agents, including agents hosted by VS Code-compatible
  editors. SQLite is the only persistent state database:
  ~/.district/district.db. There is no JSON session registry or state mirror.

COMMAND MODEL
  Session creation, attachment, execution, observation and agent reuse all use
  the same canonical session model.

BACKENDS
  macOS/Linux   tmux-backed persistent terminals with attach/read-only
                capture.
  Windows       ConPTY-backed persistent PowerShell sessions when supported.
  Process-only  Direct IDE/extension-host work is observable without
                pretending it is reconnectable when no persistent PTY exists.

CORE COMMANDS
  district                         Create and attach to a persistent session.
  district COMMAND [ARG ...]       Create, run COMMAND, then keep the shell
                                 alive.
  district ID                      Attach to reconnectable session ID.
  district ID --exec CMD...        Execute CMD in an existing managed session.
  district ID --send TEXT...       Type TEXT into the session and press Enter.
  district ID --send-key KEY       Press one key (Enter, C-c, Escape, Up, ...).
  district ID --kill               Terminate/remove the session.
  district --kill-all              Terminate/remove every local session.
  district --list [AGENT ...]      List local sessions, optionally by provider.
  district --status [--json]    Aggregate local and configured remote status.
  district --status --json --local-only
                                 Machine API used by editor integrations.
  district --watch [FILTER ...]    Interactive/read-only multi-session
                                 dashboard.
  district --watch --once          Render one dashboard snapshot and exit.
  district --view ID               Read-only view of one session.
  district --web [--host HOST] [--port PORT] [--no-browser]
             [--expose BACKEND] [--charter-short]
             [--charter-alias NAME] [--charter-authtoken TOKEN]
                                 Start/reuse the live WebSocket dashboard.
                                 `--expose` (ngrok|pinggy|tunnl|runlocal)
                                 publishes it through tools.charter; defaults
                                 come from [tool.district.charter].
  district --web --qr [--expose BACKEND]
                                 Print a QR code (with its link beside it) that
                                 pairs a phone on the same Wi-Fi/LAN and opens
                                 the console there, already signed in. With a
                                 charter configured the phone continues on the
                                 public URL. Every device must be paired.
  district --web info              Show the validated running server URL.
  district --web --startup         Enable per-user startup.
  district --web --no-startup      Disable per-user startup.
  district --web --startup state   Show startup state.
  district --history [FILTER ...]  Query durable command history from SQLite.
  district --events [FILTER ...]   Query normalized lifecycle events from
                                 SQLite.

AGENT ROUTING
  district --agent [CMD ...]       Reuse/create the current provider session.
  district --ensure [CMD ...]      Equivalent idempotent agent-session entry.
  district --is-agent-shell        Return success when current shell is
                                 agent-owned.
  district --session-name NAME ...
                                 Enter/control the district session recorded
                                 under agent session NAME (same actions
                                 as district ID).

CONTROL PLANE
  district --daemon-start          Start the local authenticated event/PTY
                                 daemon.
  district --daemon-stop           Stop it.
  district --daemon-status         Print running/stopped.
  district --subscribe [--once]    Stream normalized daemon events as JSON
                                 lines.
  district --doctor [--full]       Validate platform, SQLite, daemon and hooks.
  district --test                  Run the install self-check; prints a JSON
                                 report of every check to stdout.
  district --purge                 Apply configured privacy/retention policy
                                 now.

REMOTE EXECUTION HOSTS
  district --hosts
  district --host-add NAME SSH_TARGET [DISTRICT_COMMAND]
  district --host-remove NAME

INTROSPECTION INSIDE A MANAGED SESSION
  district --id                    Print current short session ID.
  district --session-name          Print the agent session name
                               (DEVIN_SESSION_ID, CLAUDE_CODE_SESSION_ID, ...).
  district --status                Print current session metadata.

DATABASE AND METADATA
  Canonical state is stored only in ~/.district/district.db.

STATE AND EVENTS
  The normalized SQLite schema stores sessions, commands, events, observed
  processes and remote-host definitions transactionally. Command lifecycle
  hooks provide authoritative command start/finish/exit information;
  process-tree inspection is the fallback. Stable host/session/process
  identities avoid PID reuse ambiguity. The daemon publishes the same
  normalized events consumed by --watch and VS Code, so monitoring surfaces
  do not invent separate state.

PRIVACY
  Sensitive command material is redacted before persistence. Retention applies
  to persisted command/event records, and bounded output buffers are
  transient. `district --purge` applies the retention policy immediately.

AGENT USAGE
  Native provider hooks are preferred; editor/environment/process attribution
  is used as a progressively weaker fallback and is recorded with each
  session.

HEADLESS OPERATION
  --exec, --status --json, --subscribe and lifecycle endpoints are designed
  for agents, editors, automation and remote execution hosts.

EDITOR INTEGRATION
  The District Terminal Manager extension never reads a JSON state file. It
  calls the SQLite-backed control-plane API
  (`district --status --json --local-only`) and subscribes to daemon events.
  VS Code, Cursor, Windsurf and VSCodium are supported where their
  extension/terminal APIs are compatible. IDE direct processes remain
  process-only unless a reconnectable PTY truly exists.

REMOTE/CONTAINER MODEL
  The execution host owns the backend and database. Remote SSH, WSL and
  container-style workflows are represented as execution hosts rather than
  assuming the desktop OS is where commands run. `--status` can aggregate
  configured hosts over SSH; `--local-only` prevents recursive aggregation.

EXIT AND PROCESS SEMANTICS
  district does not redefine shell `exit`. Exiting a child application returns
  to the managed shell; exiting the top-level managed shell detaches all
  attached clients and respawns a fresh shell. The session itself stays
  alive until `district ID --kill` or the tmux session is destroyed. Managed
  shells use a recursion guard so invoking `district` does not accidentally
  nest persistent sessions.

RECOVERY / TROUBLESHOOTING
  Use `district --doctor --full`; SQLite corruption is quarantined and rebuilt,
  live backends are reconciled, and schema upgrades run transactionally.

UPDATES
  district --version               Print the installed version.
  district --check-updates         Check GitHub Releases now (alias:
                                 --update-check).
  district --update [VERSION]      Install the latest (or a specific) release.
                                 Live checkouts are never rewritten.
  district --settings              Print every effective setting as JSON.
  district --make-release [patch|minor|major|X.Y.Z] [--dry-run] [--no-verify]
                       [--watch]
                               Maintainer-only: verify, bump, tag and push
                                 so CI publishes the release.
  Interactive invocations show a one-line notice when a newer release is
  cached. Policy lives in ~/.district/update.json (check: always |
  hourly | daily | weekly | never, repository, tag_prefix, channel,
  timeout_seconds). DISTRICT_DISABLE_UPDATE_CHECK=1 turns checks off;
  DISTRICT_UPDATE_REPO=owner/repo overrides the release repository; GH_TOKEN
  or GITHUB_TOKEN authenticates against a private repository.

INSTALLATION ALIAS
  Installation supports a configurable alias. `--force-alias` permits
  replacing a District Terminal-owned conflicting alias while preserving
  unrelated user commands.

RETURN CODES
  0 means success, 1 means an unavailable/failed runtime operation, and 2
  means invalid public CLI usage or an unknown requested session.

IDENTIFIERS
  Public session IDs are six lowercase hexadecimal characters. Internal stable
  UUIDs identify sessions across process/PID changes. Provider session IDs and
  editor/workspace metadata are stored separately when available.

INTERNAL INTERFACE
  DISTRICT_SESSION_ID marks the current managed session for shell recursion
  guards; stable UUIDs and backend-neutral metadata drive the control plane.
  DISTRICT_MCP_READ_ONLY (or `district --mcp --read-only`) hides execution
  tools from MCP clients.
  DISTRICT_DISABLE_AGENT suppresses agent-shell detection, opting the current
  shell out of automatic agent routing.

INTERNAL HOOKS
  --cursor-hook, --devin-hook, --codex-hook, --claude-hook,
  --lifecycle-start, --lifecycle-finish, --agent-state, --handle-uri and
  --mcp are integration endpoints and normally should not be invoked
  manually.

HELP
  district -h | district --help      Show this complete manual.
"""


class Cli:
    """Parse and execute the public district command."""

    def __init__(self, *, service: District) -> None:
        """Initialize the CLI.

        Args:
            service: Service.

        """
        self.service: District = service

    @classmethod
    def remote_payload_rows(
        cls,
        *,
        host: RemoteHost,
        payload: JsonValue,
    ) -> list[StatusRow]:
        """Convert one remote payload to status rows.

        Args:
            host: Remote host.
            payload: Parsed payload.

        Returns:
            Status rows.

        """
        if not _is_json_object(payload):
            return []
        sessions: JsonValue | None = payload.get("sessions")
        if isinstance(sessions, list):
            return [
                normalized
                for item in sessions
                if _is_json_object(item)
                and (
                    normalized := cls._normalize_remote_row(
                        host=host,
                        row=item,
                    )
                )
            ]
        error: JsonValue | None = payload.get("error")
        if error is None:
            return []
        return [
            {
                "host": host.name,
                "agent": "-",
                "surface": "remote",
                "state": "UNREACHABLE",
                "project": "-",
                "command": cls.json_text(value=error),
            },
        ]

    @classmethod
    def web_options(
        cls,
        *,
        args: tuple[str, ...],
    ) -> tuple[str, int, bool, Exposure] | None:
        """Parse web-server options.

        Args:
            args: Web arguments.

        Returns:
            Host, port, browser flag, and exposure choices, or None for
            invalid arguments.

        """
        host: str = WebConstants.default_host
        port: int = WebConstants.default_port
        browser: bool = True
        try:
            exposure: Exposure = load_exposure()
        except (TypeError, ValueError) as error:
            _ = stderr.write(f"Invalid charter settings: {error}\n")
            return None
        index: int = 0
        while index < len(args):
            value: str = args[index]
            if value == "--no-browser":
                browser = False
                index += 1
                continue
            if value in {"--charter-short", "--no-charter-short"}:
                exposure = replace(
                    exposure,
                    shorten=value == "--charter-short",
                )
                index += 1
                continue
            parsed: tuple[str, int, Exposure] | None = cls._web_option(
                args=args,
                index=index,
                host=host,
                port=port,
                exposure=exposure,
            )
            if parsed is None:
                return None
            host, port, exposure = parsed
            index += 2
        return host, port, browser, exposure

    @classmethod
    def print_entries(
        cls,
        *,
        entries: tuple[SessionEntry, ...],
    ) -> None:
        """Print live sessions with observability metadata.

        Args:
            entries: Entries.

        """
        if not entries:
            _ = stdout.write("No district sessions.\n")
            return
        headers: tuple[str, ...] = (
            "ID",
            "TYPE",
            "AGENT",
            "AGENT SESSION",
            "SURFACE",
            "EDITOR",
            "STATE",
            "CURRENT",
            "CWD",
            "COMMAND",
        )
        snapshots: tuple[SessionSnapshot, ...] = tuple(
            Monitor.snapshot(entry=entry) for entry in entries
        )
        rows: tuple[tuple[str, ...], ...] = tuple(
            (
                entry.id,
                entry.initiator or "-",
                entry.agent or "-",
                entry.agent_session or "-",
                entry.surface or "-",
                entry.editor or "-",
                snapshot.state,
                snapshot.current_command or "-",
                entry.cwd or "-",
                entry.command,
            )
            for entry, snapshot in zip(
                entries,
                snapshots,
                strict=True,
            )
        )
        widths: tuple[int, ...] = tuple(
            max(
                len(headers[index]),
                *(len(row[index]) for row in rows),
            )
            for index in range(len(headers))
        )
        _ = stdout.write(
            "  ".join(
                f"{value:<{width}}"
                for value, width in zip(
                    headers,
                    widths,
                    strict=True,
                )
            )
            + "\n",
        )
        _ = stdout.write(
            "  ".join("─" * width for width in widths) + "\n",
        )
        for row, entry in zip(rows, entries, strict=True):
            prefix: str = "  ".join(
                f"{value:<{width}}"
                for value, width in zip(
                    row[:-1],
                    widths[:-1],
                    strict=True,
                )
            )
            _ = stdout.write(
                f"{prefix}  {cls._command_link(entry=entry)}\n",
            )

    @classmethod
    def integration_test(
        cls,
        *,
        home: Path,
        args: tuple[str, ...],
    ) -> int:
        """Run deterministic integration qualification scenarios.

        Args:
            home: User home directory.
            args: Integration-test arguments.

        Returns:
            Exit status.

        """
        if not args:
            _ = stderr.write(
                "Usage: district --test-integration "
                "synthetic [PROVIDER|all] | concurrent | real PROVIDER\n",
            )
            return 2
        try:
            results: tuple[IntegrationResult, ...] | None = (
                cls._integration_results(home=home, args=args)
            )
        except ValueError as error:
            _ = stderr.write(f"{error}\n")
            return 2
        if results is None:
            _ = stderr.write("Invalid --test-integration arguments.\n")
            return 2
        return cls._report_results(results=results)

    @staticmethod
    def hook_present(*, path: Path) -> bool:
        """Check a startup file for District Terminal's lifecycle marker.

        Args:
            path: Path.

        Returns:
            The resulting value.

        """
        return (
            path.is_file()
            and "# >>> district command lifecycle >>>"
            in path.read_text(encoding="utf-8")
        )

    @staticmethod
    def history(*, home: Path, filters: tuple[str, ...]) -> int:
        """Print durable activity history, optionally filtered.

        Args:
            home: Home.
            filters: Filters.

        Returns:
            The resulting value.

        """
        lowered: tuple[str, ...] = tuple(value.lower() for value in filters)
        for line in History(home=home).lines():
            if not lowered or any(value in line.lower() for value in lowered):
                _ = stdout.write(line + "\n")
        return 0

    @staticmethod
    def subscribe(*, home: Path, once: bool) -> int:
        """Stream normalized daemon events as JSON lines.

        Args:
            home: Home.
            once: Once.

        Returns:
            The resulting value.

        """
        if not DistrictDaemon.ping(home=home):
            _ = DistrictDaemon.start(home=home)
        try:
            subscriber: EventSubscriber = EventSubscriber(home=home)
        except (ConnectionError, OSError):
            _ = stderr.write("District Terminal daemon is not available.\n")
            return 1
        try:
            while True:
                payload: str | None = subscriber.receive(timeout=1.0)
                if payload is None:
                    if once:
                        return 0
                    continue
                _ = stdout.write(payload + "\n")
                stdout.flush()
                if once:
                    return 0
        except (KeyboardInterrupt, EOFError, OSError):
            return 0
        finally:
            subscriber.close()

    @staticmethod
    def events(*, home: Path, filters: tuple[str, ...]) -> int:
        """Print normalized event records from SQLite.

        Args:
            home: Home.
            filters: Filters.

        Returns:
            The resulting value.

        """
        for line in DistrictDatabase(home=home).event_json(
            filters=filters,
        ):
            _ = stdout.write(line + "\n")
        return 0

    @staticmethod
    def purge(*, home: Path) -> int:
        """Apply default privacy retention policy immediately.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        commands, events = DistrictDatabase(home=home).purge(
            policy=PrivacyPolicy.load(home=home),
        )
        _ = stdout.write(
            f"Purged commands={commands} events={events}\n",
        )
        return 0

    @staticmethod
    def hosts(*, home: Path) -> int:
        """Print configured remote execution hosts.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        for host in HostRepository(home=home).read():
            _ = stdout.write(
                (
                    f"{host.name}\t{host.target}\t"
                    f"{host.command}\t{host.enabled}\n"
                ),
            )
        return 0

    @staticmethod
    def host_add(*, home: Path, args: tuple[str, ...]) -> int:
        """Create or replace a remote host definition.

        Args:
            home: Home.
            args: Args.

        Returns:
            The resulting value.

        """
        if len(args) not in {
            Constants.host_add_min_args,
            Constants.host_add_max_args,
        }:
            _ = stderr.write(
                "Usage: district --host-add NAME SSH_TARGET "
                "[DISTRICT_COMMAND]\n",
            )
            return 2
        HostRepository(home=home).upsert(
            host=RemoteHost(
                name=args[0],
                target=args[1],
                command=(
                    args[2]
                    if len(args) == Constants.host_add_max_args
                    else "district"
                ),
            ),
        )
        return 0

    @staticmethod
    def host_remove(*, home: Path, args: tuple[str, ...]) -> int:
        """Remove a remote host definition.

        Args:
            home: Home.
            args: Args.

        Returns:
            The resulting value.

        """
        if len(args) != Constants.host_remove_args:
            _ = stderr.write("Usage: district --host-remove NAME\n")
            return 2
        HostRepository(home=home).remove(name=args[0])
        return 0

    @staticmethod
    def lifecycle_start(*, home: Path, args: tuple[str, ...]) -> int:
        """Record an authoritative command start from an internal shell hook.

        Args:
            home: Home.
            args: Args.

        Returns:
            The resulting value.

        """
        session_id: str = environ.get("DISTRICT_SESSION_ID", "")
        if (
            not session_id
            or len(args) < Constants.lifecycle_start_min_args
            or args[0] != "--"
        ):
            return 2
        command_id: str = CommandLifecycle(home=home).start(
            session_id=session_id,
            command=" ".join(args[1:]),
            cwd=str(object=Path.cwd()),
        )
        _ = stdout.write(command_id + "\n")
        return 0

    @staticmethod
    def lifecycle_finish(*, home: Path, args: tuple[str, ...]) -> int:
        """Record authoritative command completion from an internal shell hook.

        Args:
            home: Home.
            args: Args.

        Returns:
            The resulting value.

        """
        session_id: str = environ.get("DISTRICT_SESSION_ID", "")
        if not session_id or len(args) != Constants.lifecycle_finish_args:
            return 2
        try:
            exit_code: int = int(args[1])
        except ValueError:
            return 2
        CommandLifecycle(home=home).finish(
            session_id=session_id,
            command_id=args[0],
            exit_code=exit_code,
        )
        return 0

    @staticmethod
    def agent_state(*, home: Path, args: tuple[str, ...]) -> int:
        """Record a provider-supplied normalized agent state.

        Args:
            home: Home.
            args: Args.

        Returns:
            The resulting value.

        """
        allowed: frozenset[str] = frozenset(
            {
                "EXECUTING",
                "THINKING",
                "WAITING_FOR_TOOL",
                "WAITING_FOR_USER",
                "IDLE",
            },
        )
        if (
            len(args) != Constants.agent_state_args
            or args[0].upper() not in allowed
        ):
            return 2
        session_id: str = environ.get("DISTRICT_SESSION_ID", "")
        if not session_id:
            return 2
        entry: SessionEntry | None = next(
            (
                item
                for item in SessionRepository(home=home).read()
                if item.id == session_id
            ),
            None,
        )
        if entry is None:
            return 2
        state: str = args[0].upper()
        details: EventDetails = EventDetails(
            session_uuid=entry.uuid,
            session_id=entry.id,
            agent=entry.agent,
            agent_session=entry.agent_session,
            state=state,
            cwd=entry.cwd,
            surface=entry.surface,
            editor=entry.editor,
        )
        event: DistrictEvent = DistrictEvent.create(
            event_type=EventTypes.agent_state,
            host_id=(entry.host_id or IdentityStore(home=home).host_id()),
            details=details,
        )
        EventRecorder(home=home).record(event=event)
        return 0

    @staticmethod
    def early_daemon(
        *,
        args: tuple[str, ...],
        home: Path,
    ) -> int | None:
        """Dispatch daemon commands.

        Args:
            args: Args.
            home: Home.

        Returns:
            Exit status when handled, otherwise None.

        """
        if args[:1] == ("--daemon",):
            return DistrictDaemon(home=home).run()
        if args == ("--daemon-start",):
            return 0 if DistrictDaemon.start(home=home) else 1
        if args == ("--daemon-stop",):
            return 0 if DistrictDaemon.stop(home=home) else 1
        if args == ("--daemon-status",):
            running: bool = DistrictDaemon.ping(home=home)
            _ = stdout.write(
                ("running" if running else "stopped") + "\n",
            )
            return 0 if running else 1
        return None

    @staticmethod
    def parse_port(*, value: str) -> int | None:
        """Parse and validate a TCP port.

        Args:
            value: Port text.

        Returns:
            Parsed port, or None for invalid input.

        """
        try:
            port: int = int(value)
        except ValueError:
            _ = stderr.write("--port must be an integer.\n")
            return None
        if not 0 <= port <= Constants.maximum_port:
            _ = stderr.write(
                "--port must be between 0 and 65535.\n",
            )
            return None
        return port

    @staticmethod
    def parse_remote_payload(*, raw: str) -> JsonValue:
        """Parse one remote status response.

        Args:
            raw: Raw remote response.

        Returns:
            Parsed JSON value or an error object.

        """
        try:
            return cast(typ="JsonValue", val=loads(s=raw))
        except ValueError:
            return {"error": raw}

    @staticmethod
    def json_text(*, value: JsonValue) -> str:
        """Render a JSON value as compact text.

        Args:
            value: JSON value.

        Returns:
            Text representation.

        """
        if isinstance(value, str):
            return value
        return dumps(
            obj=value,
            separators=(",", ":"),
        )

    @staticmethod
    def print_global_status(*, rows: list[StatusRow]) -> None:
        """Print human-readable global status.

        Args:
            rows: Status rows.

        """
        _ = stdout.write(
            "HOST             AGENT        SURFACE      "
            "STATE          PROJECT              COMMAND\n",
        )
        for row in rows:
            project: str = str(object=row.get("project", "-"))
            command: str = str(object=row.get("command", "-")) or "—"
            _ = stdout.write(
                f"{row.get('host', '-')!s:<16} "
                f"{row.get('agent', '-')!s:<12} "
                f"{row.get('surface', '-')!s:<12} "
                f"{row.get('state', '-')!s:<14} "
                f"{project:<20} {command}\n",
            )
        executing: int = sum(
            1 for row in rows if row.get("state") in {"ACTIVE", "EXECUTING"}
        )
        waiting: int = sum(
            1
            for row in rows
            if str(object=row.get("state", "")).startswith("WAITING")
        )
        idle: int = sum(1 for row in rows if row.get("state") == "IDLE")
        _ = stdout.write(
            f"\n{len(rows)} sessions · "
            f"{executing} executing · "
            f"{waiting} waiting · "
            f"{idle} idle\n",
        )

    @staticmethod
    def update_notice_path(*, args: tuple[str, ...]) -> bool:
        """Return whether an invocation is a human-facing interactive one.

        Machine endpoints, hooks and scripted calls never trigger update
        checks or notices.

        Args:
            args: Command-line arguments.

        Returns:
            True when a refresh and notice are appropriate.

        """
        if not args:
            return True
        first: str = args[0]
        if first.startswith("-"):
            if first == "--status":
                return "--json" not in args
            return first in Constants.update_notice_options
        if first in Constants.update_silent_commands:
            return False
        if fullmatch(pattern=r"[0-9a-f]{6}", string=first):
            return len(args) == 1
        return True

    @staticmethod
    def module(*, name: str) -> ModuleType:
        """Import a sibling module on first use.

        Args:
            name: Module name inside this package.

        Returns:
            The imported module.

        """
        return import_module(name=f".{name}", package=__package__)

    @staticmethod
    def print_version(*, args: tuple[str, ...], home: Path) -> int:
        """Print the installed version.

        Args:
            args: Unused arguments.
            home: Unused home.

        Returns:
            Exit status.

        """
        _ = (args, home)
        _ = stdout.write(f"district {Product.version}\n")
        return 0

    def run(
        self,
        *,
        arguments: tuple[str, ...] | None = None,
    ) -> int:
        """Execute one command invocation.

        Args:
            arguments: Arguments.

        Returns:
            The resulting value.

        """
        args: tuple[str, ...] = () if arguments is None else arguments
        home: Path = Path(environ.get("HOME", str(object=Path.home())))
        self.update_notice(args=args, home=home)
        if (early := self.early(args=args, home=home)) is not None:
            return early
        managed_id: str | None = environ.get("DISTRICT_SESSION_ID")
        if managed_id is not None:
            return self.managed(args=args, session_id=managed_id)
        return self.dispatch(args=args)

    def update_notice(self, *, args: tuple[str, ...], home: Path) -> None:
        """Refresh the release cache and print a pending-update notice.

        Args:
            args: Command-line arguments.
            home: User home directory.

        """
        if (
            environ.get("DISTRICT_SESSION_ID")
            or not self.update_notice_path(args=args)
            or not stderr.isatty()
        ):
            return
        update: ModuleType = self.module(name="update")
        update.maybe_refresh(home=home)
        update.notify(home=home)

    def web_special(
        self,
        *,
        args: tuple[str, ...],
        dashboard: WebDashboard,
        startup: WebStartup,
    ) -> int | None:
        """Handle non-server web subcommands.

        Args:
            args: Web arguments.
            dashboard: Web dashboard.
            startup: Startup manager.

        Returns:
            Exit status when handled, otherwise None.

        """
        if args == ("info",):
            return self._web_info(dashboard=dashboard)
        if args == ("--startup", "state"):
            return self._print_startup(state=startup.state())
        if args == ("--startup",):
            return self._print_startup(state=startup.enable())
        if args == ("--no-startup",):
            return self._print_startup(state=startup.disable())
        return None

    def cleanup(self, *, args: tuple[str, ...]) -> int:
        """Run internal session cleanup for a finished session.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        if len(args) != Constants.cleanup_args:
            return Constants.cleanup_args
        self.service.cleanup(session_id=args[1])
        return 0

    def managed(
        self,
        *,
        args: tuple[str, ...],
        session_id: str,
    ) -> int:
        """Handle an invocation arriving from inside a district terminal.

        Args:
            args: Args.
            session_id: Session id.

        Returns:
            The resulting value.

        """
        if args in (("--id",), ("--agent",), ("--ensure",)):
            _ = stdout.write(f"{session_id}\n")
        elif args == ("--session-name",):
            return self.session_name(session_id=session_id)
        elif args == ("--status",):
            self._print_status(
                entry=self.service.status(
                    session_id=session_id,
                ),
            )
        elif args and args[0] == "--exec":
            return self.exec(
                args=args[1:],
                session_id=session_id,
            )
        return 0

    def dispatch(self, *, args: tuple[str, ...]) -> int:
        """Handle a normal invocation outside a district terminal.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        known: int | None = self.known(args=args)
        if known is not None:
            return known
        if args and self._is_id(value=args[0]):
            action: int | None = self.by_id(
                args=args,
                session_id=args[0],
            )
            if action is not None:
                return action
        if args and args[0].startswith("--"):
            return self._option(args=args)
        if not self._persistent_backend_available():
            _ = stderr.write(
                "No persistent session backend is available.\n",
            )
            return 127
        return self.service.create(initial_command=args)

    def known(self, *, args: tuple[str, ...]) -> int | None:
        """Handle fixed commands that need backend routing.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        if args == ("--id",):
            _ = stderr.write(
                "Not inside a district terminal.\n",
            )
            return 1
        if args == ("--session-name",):
            _ = stderr.write(
                "Not inside a district terminal.\n",
            )
            return 1
        if args and args[0] == "--session-name":
            return self.session_by_name(args=args[1:])
        if args and args[0] in ("--agent", "--ensure"):
            if not self._persistent_backend_available():
                _ = stderr.write(
                    "No persistent session backend is available.\n",
                )
                return 127
            return self.service.ensure_agent(
                initial_command=args[1:],
            )
        return None

    def session_name(self, *, session_id: str) -> int:
        """Print the agent session name for the current district session.

        Args:
            session_id: Session id.

        Returns:
            The resulting value.

        """
        stale: bool = False
        try:
            recorded: str = self.service.status(
                session_id=session_id,
            ).agent_session
        except KeyError:
            recorded = ""
            stale = True
        name: str = (
            environ.get("DISTRICT_AGENT_SESSION", "")
            or recorded
            or next(
                (
                    value
                    for marker in Constants.session_name_markers
                    if (value := environ.get(marker, ""))
                ),
                "",
            )
        )
        if name:
            _ = stdout.write(f"{name}\n")
            return 0
        if stale:
            _ = stderr.write(
                f"Unknown district session: {session_id}\n",
            )
            return 2
        _ = stderr.write(
            "No agent session name recorded for this district session.\n",
        )
        return 1

    def session_by_name(self, *, args: tuple[str, ...]) -> int:
        """Route an agent-session-name invocation like a session-ID one.

        Args:
            args: Session name followed by optional session actions.

        Returns:
            The resulting value.

        """
        if not args:
            return 2
        matches: list[SessionEntry] = [
            entry
            for entry in self.service.list_entries()
            if entry.agent_session == args[0]
        ]
        if not matches:
            _ = stderr.write(
                f"Unknown session name: {args[0]}\n",
            )
            return 2
        entry: SessionEntry = max(
            matches,
            key=attrgetter("created_at"),
        )
        action: int | None = self.by_id(
            args=(entry.id, *args[1:]),
            session_id=entry.id,
        )
        return action if action is not None else 2

    def by_id(
        self,
        *,
        args: tuple[str, ...],
        session_id: str,
    ) -> int | None:
        """Route a session-ID invocation to attach or kill.

        Args:
            args: Args.
            session_id: Session id.

        Returns:
            The resulting value.

        """
        if args == (session_id, "--kill"):
            return self.kill(session_id=session_id)
        if args[1:2] == ("--exec",):
            return self.exec(
                args=args[2:],
                session_id=session_id,
            )
        if args[1:2] == ("--send",):
            return self.send(
                args=args[2:],
                session_id=session_id,
                key_mode=False,
            )
        if args[1:2] == ("--send-key",):
            return self.send(
                args=args[2:],
                session_id=session_id,
                key_mode=True,
            )
        if args == (session_id,):
            return self.attach(session_id=session_id)
        return None

    def attach(self, *, session_id: str) -> int:
        """Attach to a live session.

        Args:
            session_id: Session id.

        Returns:
            The resulting value.

        """
        try:
            return self.service.attach(
                session_id=session_id,
            )
        except KeyError:
            _ = stderr.write(
                f"Unknown district session: {session_id}\n",
            )
            return 2

    def exec(
        self,
        *,
        args: tuple[str, ...],
        session_id: str | None,
    ) -> int:
        """Run a command inside a live session without attaching.

        Args:
            args: Args.
            session_id: Session id.

        Returns:
            The resulting value.

        """
        if session_id is None:
            entries: tuple[
                SessionEntry,
                ...,
            ] = self.service.list_entries()
            if len(entries) != 1:
                prefix: str = (
                    "--exec needs exactly one live district session; "
                )
                _ = stderr.write(
                    prefix + "use district ID --exec CMD...\n",
                )
                return 2
            session_id = entries[0].id
        if not args:
            _ = stderr.write(
                "Missing command after --exec.\n",
            )
            return 2
        try:
            command: str = (
                args[0] if len(args) == 1 else join(split_command=args)
            )
            result: ExecResult = self.service.execute(
                session_id=session_id,
                command=command,
            )
        except KeyError:
            _ = stderr.write(
                f"Unknown district session: {session_id}\n",
            )
            return 2
        except TimeoutError:
            _ = stderr.write(
                f"Timed out waiting for district session {session_id}.\n",
            )
            return 124
        if result.output:
            _ = stdout.write(result.output)
            if not result.output.endswith("\n"):
                _ = stdout.write("\n")
            stdout.flush()
        return result.exit_status

    def send(
        self,
        *,
        args: tuple[str, ...],
        session_id: str | None,
        key_mode: bool,
    ) -> int:
        """Inject literal text or one key press into a live session.

        Args:
            args: Args.
            session_id: Session id.
            key_mode: Whether args carry a single key name rather than
                literal text.

        Returns:
            The resulting value.

        """
        if session_id is None:
            entries: tuple[
                SessionEntry,
                ...,
            ] = self.service.list_entries()
            if len(entries) != 1:
                prefix: str = (
                    "--send needs exactly one live district session; "
                )
                _ = stderr.write(
                    prefix + "use district ID --send TEXT...\n",
                )
                return 2
            session_id = entries[0].id
        if not args:
            _ = stderr.write(
                "Missing input after --send/--send-key.\n",
            )
            return 2
        text: str = ""
        keys: tuple[str, ...] = ()
        if key_mode:
            if len(args) != 1:
                _ = stderr.write(
                    "--send-key takes exactly one key name.\n",
                )
                return 2
            keys = (args[0],)
        else:
            text = args[0] if len(args) == 1 else join(split_command=args)
            keys = ("Enter",)
        try:
            self.service.send(
                session_id=session_id,
                text=text,
                keys=keys,
            )
        except KeyError:
            _ = stderr.write(
                f"Unknown district session: {session_id}\n",
            )
            return 2
        except (
            ValueError,
            RuntimeError,
            CalledProcessError,
            OSError,
        ) as failed:
            _ = stderr.write(
                f"Could not send input to {session_id}: {failed}\n",
            )
            return 2
        return 0

    def kill(self, *, session_id: str) -> int:
        """Kill a live session.

        Args:
            session_id: Session id.

        Returns:
            The resulting value.

        """
        try:
            self.service.kill(session_id=session_id)
        except KeyError:
            _ = stderr.write(
                f"Unknown district session: {session_id}\n",
            )
            return 2
        _ = stdout.write(
            f"Killed district session {session_id}\n",
        )
        return 0

    def kill_all(self) -> int:
        """Kill every live session, continuing past individual failures.

        Returns:
            0 when every session was killed (or none existed), else 1.

        """
        entries: tuple[SessionEntry, ...] = self.service.list_entries()
        if not entries:
            _ = stdout.write("No district sessions to kill.\n")
            return 0
        failed: int = 0
        for entry in entries:
            try:
                self.service.kill(session_id=entry.id)
            except (KeyError, OSError, CalledProcessError) as error:
                failed += 1
                _ = stderr.write(
                    f"Could not kill district session {entry.id}: {error}\n",
                )
                continue
            _ = stdout.write(f"Killed district session {entry.id}\n")
        _ = stdout.write(
            f"Killed {len(entries) - failed} of {len(entries)} "
            "district session(s).\n",
        )
        return 1 if failed else 0

    def early(
        self,
        *,
        args: tuple[str, ...],
        home: Path,
    ) -> int | None:
        """Handle commands not requiring a persistent-session backend.

        Args:
            args: Args.
            home: Home.

        Returns:
            The resulting value.

        """
        handlers: tuple[
            Callable[..., int | None],
            ...,
        ] = (
            self.early_base,
            self.early_observation,
            self.early_daemon,
            self.early_control,
        )
        for handler in handlers:
            result: int | None = handler(
                args=args,
                home=home,
            )
            if result is not None:
                return result
        return None

    def early_base(
        self,
        *,
        args: tuple[str, ...],
        home: Path,
    ) -> int | None:
        """Dispatch basic and provider-hook commands.

        Args:
            args: Args.
            home: Home.

        Returns:
            Exit status when handled, otherwise None.

        """
        if args in (("-h",), ("--help",)):
            _ = stdout.write(Constants.help_text)
            return 0
        if args == ("--is-agent-shell",):
            return 0 if AgentDetector.is_agent_shell() else 1
        if args[:1] == ("--mcp",) and set(args[1:]) <= {"--read-only"}:
            return DistrictMcpServer(
                service=self.service,
                read_only="--read-only" in args
                or bool(environ.get("DISTRICT_MCP_READ_ONLY")),
            ).serve()
        handlers: dict[str, HookRunner] = {
            "--cursor-hook": CursorHooks.run_hook,
            "--devin-hook": DevinHooks.run_hook,
            "--codex-hook": CodexHooks.run_hook,
            "--claude-hook": ClaudeHooks.run_hook,
        }
        if args and (handler := handlers.get(args[0])) is not None:
            return handler(home=home)
        if args[:1] == ("--cleanup",):
            return self.cleanup(args=args)
        return self.early_updates(args=args, home=home)

    def early_updates(
        self,
        *,
        args: tuple[str, ...],
        home: Path,
    ) -> int | None:
        """Dispatch version, settings, self-update and release commands.

        Modules are imported lazily to keep ordinary startup fast.

        Args:
            args: Args.
            home: Home.

        Returns:
            Exit status when handled, otherwise None.

        """
        if not args:
            return None
        exact: dict[str, Callable[..., int]] = {
            "--version": self.print_version,
            "--settings": self.print_settings,
            "--check-updates": self.check_updates,
            "--update-check": self.check_updates,
            "--update-refresh": self.refresh_updates,
        }
        prefixed: dict[str, Callable[..., int]] = {
            "--update": self.update,
            "--make-release": self.make_release,
            "--handle-uri": self.handle_uri,
        }
        handler: Callable[..., int] | None = (
            exact.get(args[0]) if len(args) == 1 else None
        ) or prefixed.get(args[0])
        if handler is None:
            return None
        return handler(args=args, home=home)

    def print_settings(self, *, args: tuple[str, ...], home: Path) -> int:
        """Print every effective setting as JSON.

        Args:
            args: Unused arguments.
            home: Home.

        Returns:
            Exit status.

        """
        _ = args
        document: object = self.module(name="settings").settings_document(
            home=home,
        )
        _ = stdout.write(dumps(obj=document, indent=2) + "\n")
        return 0

    def check_updates(self, *, args: tuple[str, ...], home: Path) -> int:
        """Force an update check.

        Args:
            args: Unused arguments.
            home: Home.

        Returns:
            Exit status.

        """
        _ = args
        return cast(
            typ="int",
            val=self.module(name="update").check_updates(home=home),
        )

    def refresh_updates(self, *, args: tuple[str, ...], home: Path) -> int:
        """Run the detached cache refresh entry point.

        Args:
            args: Arguments.
            home: Home.

        Returns:
            Exit status.

        """
        return cast(
            typ="int",
            val=self.module(name="update").main(arguments=args, home=home),
        )

    def update(self, *, args: tuple[str, ...], home: Path) -> int:
        """Run ``--update [VERSION]``.

        Args:
            args: Arguments beginning with ``--update``.
            home: Home.

        Returns:
            Exit status.

        """
        rest: tuple[str, ...] = args[1:]
        if len(rest) > 1 or (rest and rest[0].startswith("-")):
            _ = stderr.write("usage: district --update [VERSION]\n")
            return 2
        return cast(
            typ="int",
            val=self.module(name="update").run_update(
                home=home,
                requested=rest[0] if rest else None,
            ),
        )

    def handle_uri(self, *, args: tuple[str, ...], home: Path) -> int:
        """Run the Bowman handler for one ``district://`` URI.

        Registered with the operating system by the installer.

        Args:
            args: ``--handle-uri`` followed by exactly one URI.
            home: Unused home.

        Returns:
            The handler's exit status, or 2 for a usage error.

        """
        _ = home
        if len(args) != Constants.handle_uri_args:
            _ = stderr.write("usage: district --handle-uri URI\n")
            return 2
        return cast(
            typ="int",
            val=self.module(name="url_handler").main(arguments=args[1:]),
        )

    def make_release(self, *, args: tuple[str, ...], home: Path) -> int:
        """Run the maintainer release driver.

        Args:
            args: Arguments beginning with ``--make-release``.
            home: Unused home.

        Returns:
            Exit status.

        """
        _ = home
        return cast(
            typ="int",
            val=self.module(name="release").run_cli(arguments=args[1:]),
        )

    def explain(self, *, session_id: str) -> int:
        """Explain the recorded attribution for one District session.

        Args:
            session_id: Public District session identifier.

        Returns:
            Exit status.

        """
        entry: SessionEntry | None = next(
            (
                candidate
                for candidate in self.service.list_entries()
                if candidate.id == session_id
            ),
            None,
        )
        if entry is None:
            _ = stderr.write(f"Unknown district session: {session_id}\n")
            return 2
        provider: str = entry.agent or "unknown"
        confidence: str = (
            "authoritative"
            if entry.attribution
            in {
                "execution_token",
                "provider_hook",
                "environment",
            }
            else "inferred"
            if provider != "unknown"
            else "insufficient"
        )
        _ = stdout.write(
            "Execution\n"
            f"  ID: {entry.id}\n"
            f"  UUID: {entry.uuid or '-'}\n\n"
            "Attribution\n"
            f"  Provider: {provider}\n"
            f"  Session: {entry.agent_session or '-'}\n"
            f"  Source: {entry.attribution or 'unknown'}\n"
            f"  Confidence: {confidence}\n"
            f"  Surface: {entry.surface or '-'}\n"
            f"  Editor: {entry.editor or '-'}\n"
            f"  Workspace: {entry.workspace or entry.cwd or '-'}\n\n"
            "Process\n"
            f"  PID: {entry.pid}\n"
            f"  Parent PID: {entry.parent_pid}\n"
            f"  Backend: {entry.backend}\n"
            f"  Reconnectable: {entry.reconnectable}\n",
        )
        return 0

    def early_observation(
        self,
        *,
        args: tuple[str, ...],
        home: Path,
    ) -> int | None:
        """Dispatch observation and reporting commands.

        Args:
            args: Args.
            home: Home.

        Returns:
            Exit status when handled, otherwise None.

        """
        result: int | None = self._observation_inspection(
            args=args,
            home=home,
        )
        if result is not None:
            return result
        return self._observation_actions(args=args, home=home)

    def early_control(
        self,
        *,
        args: tuple[str, ...],
        home: Path,
    ) -> int | None:
        """Dispatch host, lifecycle, list, and global-status commands.

        Args:
            args: Args.
            home: Home.

        Returns:
            Exit status when handled, otherwise None.

        """
        if args[:1] == ("--host-add",):
            return self.host_add(
                home=home,
                args=args[1:],
            )
        if args[:1] == ("--host-remove",):
            return self.host_remove(
                home=home,
                args=args[1:],
            )
        if args == ("--hosts",):
            return self.hosts(home=home)
        if args[:1] == ("--lifecycle-start",):
            return self.lifecycle_start(
                home=home,
                args=args[1:],
            )
        if args[:1] == ("--lifecycle-finish",):
            return self.lifecycle_finish(
                home=home,
                args=args[1:],
            )
        return (
            self.agent_state(
                home=home,
                args=args[1:],
            )
            if args[:1] == ("--agent-state",)
            else self.kill_all()
            if args == ("--kill-all",)
            else self.list_sessions(args=args)
            if args[:1] == ("--list",)
            else self.global_status(
                home=home,
                args=args[1:],
            )
            if (
                args[:1] == ("--status",)
                and not environ.get("DISTRICT_SESSION_ID")
            )
            else None
        )

    def list_sessions(self, *, args: tuple[str, ...]) -> int:
        """List local sessions, optionally filtered by agent.

        Args:
            args: Command arguments.

        Returns:
            Exit status.

        """
        filters: frozenset[str] = frozenset(
            value.strip().lower() for value in args[1:] if value.strip()
        )
        entries: tuple[SessionEntry, ...] = self.service.list_entries()
        if filters:
            entries = tuple(
                entry for entry in entries if entry.agent.lower() in filters
            )
        self.print_entries(entries=entries)
        return 0

    def view(self, *, filters: tuple[str, ...]) -> int:
        """Render one session read-only.

        Args:
            filters: View filters.

        Returns:
            Exit status.

        """
        if not filters:
            _ = stderr.write(
                "Missing session ID after --view.\n",
            )
            return 2
        entries: tuple[SessionEntry, ...] = tuple(
            entry
            for entry in self.service.list_entries()
            if entry.id == filters[0]
        )
        if not entries:
            _ = stderr.write(
                f"Unknown district session: {filters[0]}\n",
            )
            return 2
        _ = stdout.write(
            WatchTui(fetch=lambda: entries).once() + "\n",
        )
        return 0

    def global_status(
        self,
        *,
        home: Path,
        args: tuple[str, ...],
    ) -> int:
        """Render local and optionally remote agent execution status.

        Args:
            home: Home.
            args: Args.

        Returns:
            The resulting value.

        """
        json_mode: bool = "--json" in args
        local_only: bool = "--local-only" in args
        rows: list[StatusRow] = self._local_status_rows()
        if not local_only:
            rows.extend(self._remote_status_rows(home=home))
        if json_mode:
            _ = stdout.write(
                dumps(
                    obj={"sessions": rows},
                    separators=(",", ":"),
                )
                + "\n",
            )
            return 0
        self.print_global_status(rows=rows)
        return 0

    @classmethod
    def _doctor(cls, *, home: Path, full: bool) -> int:
        """Print execution-host, storage, daemon, and integration health.

        Args:
            home: Home.
            full: Full.

        Returns:
            The resulting value.

        """
        runtime: RuntimePlatform = RuntimePlatform.detect()
        caps: Capabilities = runtime.capabilities
        database: DistrictDatabase = DistrictDatabase(home=home)
        values: list[tuple[str, str]] = [
            ("OS", runtime.os),
            ("Architecture", runtime.architecture),
            ("Shell", runtime.shell_location or "unknown"),
            ("Backend", runtime.backend),
            ("Persistent sessions", str(object=caps.persistent_sessions)),
            ("Read-only capture", str(object=caps.read_only_capture)),
            ("Process tree", str(object=caps.process_tree)),
            ("Clipboard", str(object=caps.clipboard)),
            ("Clickable copy", str(object=caps.clickable_copy)),
            ("Notifications", str(object=caps.notifications)),
            ("Editor bridge", str(object=caps.editor_agent_bridge)),
            ("Editor", RuntimePlatform.editor() or "none"),
            ("Surface", RuntimePlatform.surface()),
            ("Agent identity", cls._identity_summary()),
            (
                "Claude MCP server",
                "registered"
                if McpRegistration.installed(home=home)
                else "missing (python install.py install --with mcp)",
            ),
            ("Database schema", str(object=database.schema_version())),
            ("Database integrity", database.integrity()),
            (
                "Daemon",
                "running" if DistrictDaemon.ping(home=home) else "stopped",
            ),
        ]
        if full:
            cls._extend_doctor_values(
                values=values,
                home=home,
                database=database,
            )
        for key, value in values:
            _ = stdout.write(f"{key:<20} {value}\n")
        return 0

    @classmethod
    def _extend_doctor_values(
        cls,
        *,
        values: list[tuple[str, str]],
        home: Path,
        database: DistrictDatabase,
    ) -> None:
        """Append full diagnostic values.

        Args:
            values: Diagnostic values.
            home: Home directory.
            database: District Terminal database.

        """
        directory: Path = state_directory(home=home)
        sessions, commands, events, hosts = database.row_counts()
        policy: PrivacyPolicy = PrivacyPolicy.load(home=home)
        powershell_profile: Path = (
            home
            / "Documents"
            / "PowerShell"
            / "Microsoft.PowerShell_profile.ps1"
        )
        values.extend(
            (
                ("Host ID", IdentityStore(home=home).host_id()),
                (
                    "Database",
                    (
                        "present"
                        if (directory / "district.db").exists()
                        else "empty"
                    ),
                ),
                ("Recovery", database.recovered_from or "not-needed"),
                ("Session rows", str(object=sessions)),
                ("Command rows", str(object=commands)),
                ("Event rows", str(object=events)),
                ("Remote hosts", str(object=hosts)),
                ("Output persisted", str(object=policy.persist_output)),
                ("History retention", f"{policy.history_days} days"),
                ("Event retention", f"{policy.event_days} days"),
                (
                    "Cursor embedded-agent hook",
                    "installed"
                    if CursorHooks.config_path(home=home).is_file()
                    else "missing",
                ),
                (
                    "Devin Local Agent hook",
                    "installed"
                    if DevinHooks.config_path(home=home).is_file()
                    else "missing",
                ),
                (
                    "Codex hook",
                    "installed"
                    if CodexHooks.config_path(home=home).is_file()
                    else "missing",
                ),
                (
                    "Claude hook",
                    "installed"
                    if ClaudeHooks.config_path(home=home).is_file()
                    else "missing",
                ),
                (
                    "Zsh lifecycle",
                    "installed"
                    if cls.hook_present(path=home / ".zshrc")
                    else "missing",
                ),
                (
                    "Bash lifecycle",
                    "installed"
                    if cls.hook_present(path=home / ".bashrc")
                    else "missing",
                ),
                (
                    "PowerShell lifecycle",
                    "installed"
                    if cls.hook_present(path=powershell_profile)
                    else "missing",
                ),
            ),
        )

    @classmethod
    def _remote_status_rows(
        cls,
        *,
        home: Path,
    ) -> list[StatusRow]:
        """Build status rows for configured remote hosts.

        Args:
            home: Home.

        Returns:
            Remote status rows.

        """
        rows: list[StatusRow] = []
        aggregator: RemoteAggregator = RemoteAggregator(
            repository=HostRepository(home=home),
        )
        for host, raw in aggregator.statuses():
            payload: JsonValue = cls.parse_remote_payload(
                raw=raw,
            )
            rows.extend(
                cls.remote_payload_rows(
                    host=host,
                    payload=payload,
                ),
            )
        return rows

    @classmethod
    def _web_option(
        cls,
        *,
        args: tuple[str, ...],
        index: int,
        host: str,
        port: int,
        exposure: Exposure,
    ) -> tuple[str, int, Exposure] | None:
        """Parse one valued web option.

        Args:
            args: Web arguments.
            index: Current argument index.
            host: Current host.
            port: Current port.
            exposure: Current exposure choices.

        Returns:
            Updated host, port, and exposure, or None for invalid input.

        """
        value: str = args[index]
        result: tuple[str, int, Exposure] | None = None
        option_index: int = index + 1
        if value not in {
            "--host",
            "--port",
            "--expose",
            "--charter-authtoken",
            "--charter-alias",
        }:
            _ = stderr.write(
                f"Unknown --web option: {value}\n",
            )
        elif option_index >= len(args):
            _ = stderr.write(
                f"Missing value for {value}.\n",
            )
        elif value == "--host":
            result = (args[option_index], port, exposure)
        elif value == "--expose":
            result = cls._expose_option(
                backend=args[option_index],
                host=host,
                port=port,
                exposure=exposure,
            )
        elif value == "--charter-authtoken":
            result = (
                host,
                port,
                replace(exposure, ngrok_authtoken=args[option_index]),
            )
        elif value == "--charter-alias":
            result = (
                host,
                port,
                replace(exposure, alias=args[option_index]),
            )
        else:
            parsed_port: int | None = cls.parse_port(value=args[option_index])
            if parsed_port is not None:
                result = (host, parsed_port, exposure)
        return result

    @staticmethod
    def _expose_option(
        *,
        backend: str,
        host: str,
        port: int,
        exposure: Exposure,
    ) -> tuple[str, int, Exposure] | None:
        """Validate a ``--expose`` backend and fold it into the exposure.

        Args:
            backend: Requested charter backend.
            host: Current host.
            port: Current port.
            exposure: Current exposure choices.

        Returns:
            Updated host, port, and exposure, or None for an unknown
            backend.

        """
        if backend not in ExposureConstants.backends:
            _ = stderr.write(
                "Unknown --web exposure backend: "
                f"{backend}. Choose from "
                + ", ".join(sorted(ExposureConstants.backends))
                + ".\n",
            )
            return None
        return host, port, replace(exposure, backend=backend)

    @staticmethod
    def _identity_summary() -> str:
        """Describe the agent identity resolved for this process.

        Returns:
            Provider, session and evidence source, or ``none``.

        """
        identity: AgentIdentity | None = AgentIdentity.from_environment()
        if identity is None:
            return "none"
        return " · ".join(
            part
            for part in (
                identity.agent,
                identity.session or "no session",
                identity.source,
            )
            if part
        )

    @staticmethod
    def _integration_results(
        *,
        home: Path,
        args: tuple[str, ...],
    ) -> tuple[IntegrationResult, ...] | None:
        """Run the requested qualification mode.

        Args:
            home: User home directory.
            args: Integration-test arguments.

        Returns:
            Qualification results, or None when the mode is invalid.

        """
        mode: str = args[0]
        if mode == "synthetic":
            provider: str = args[1] if len(args) > 1 else "all"
            return (
                IntegrationHarness.synthetic_all(home=home)
                if provider == "all"
                else (
                    IntegrationHarness.synthetic(
                        home=home,
                        provider=provider,
                    ),
                )
            )
        if mode == "concurrent":
            return IntegrationHarness.concurrency_matrix(home=home)
        if mode == "real" and len(args[1:]) == 1:
            return (
                IntegrationHarness.real(
                    home=home,
                    provider=args[1],
                ),
            )
        return None

    @staticmethod
    def _report_results(
        *,
        results: tuple[IntegrationResult, ...],
    ) -> int:
        """Print per-provider results and summarize the exit status.

        Args:
            results: Qualification results.

        Returns:
            Exit status.

        """
        for result in results:
            status: str = "PASS" if result.passed else "FAIL"
            _ = stdout.write(
                f"{status} {result.provider}: {result.detail}\n",
            )
        return 0 if all(result.passed for result in results) else 1

    @staticmethod
    def _is_id(*, value: str) -> bool:
        """Return whether a token has the public short-ID syntax.

        Args:
            value: Value.

        Returns:
            The resulting value.

        """
        return (
            fullmatch(
                pattern=r"[0-9a-f]{6}",
                string=value,
            )
            is not None
        )

    @staticmethod
    def _command_link(*, entry: SessionEntry) -> str:
        """Return a clickable reconnect command that copies on macOS.

        Args:
            entry: Entry.

        Returns:
            The resulting value.

        """
        target: str = f"district://copy?{urlencode(query={'id': entry.id})}"
        return f"\033]8;;{target}\033\\{entry.command}\033]8;;\033\\"

    @staticmethod
    def _print_status(*, entry: SessionEntry) -> None:
        """Print detailed session status.

        Args:
            entry: Entry.

        """
        _ = stdout.write(
            "".join(
                (
                    "District terminal: active\n",
                    f"ID: {entry.id}\n",
                    f"Attach: {entry.command}\n",
                    f"Created: {entry.created_at}\n",
                    f"Initiator: {entry.initiator}\n",
                    f"CWD: {entry.cwd}\n",
                    f"PID: {entry.pid}\n",
                ),
            ),
        )

    @staticmethod
    def _persistent_backend_available() -> bool:
        """Return whether this host can create persistent sessions.

        Returns:
            The resulting value.

        """
        runtime: RuntimePlatform = RuntimePlatform.detect()
        if runtime.os in {"macos", "linux"}:
            return which(cmd="tmux") is not None
        return runtime.capabilities.persistent_sessions

    @staticmethod
    def _print_startup(*, state: StartupState) -> int:
        """Render one ANSI-coherent startup status line.

        Args:
            state: State.

        Returns:
            The resulting value.

        """
        marker: str = (
            "\033[32m●\033[0m" if state.enabled else "\033[2m●\033[0m"
        )
        status: str = "enabled" if state.enabled else "disabled"
        _ = stdout.write(
            f"{marker} Web startup: {status}  "
            f"\033[2m{state.mechanism} · "
            f"{state.location}\033[0m\n",
        )
        return 0

    @staticmethod
    def _web_info(*, dashboard: WebDashboard) -> int:
        """Print the validated running web-server URL.

        Args:
            dashboard: Web dashboard.

        Returns:
            Exit status.

        """
        url: str | None = dashboard.running_url()
        if url is None:
            _ = stdout.write(
                "\033[2m●\033[0m Web server: stopped\n",
            )
            return 1
        _ = stdout.write(
            "\033[32m●\033[0m Web server: running  "
            f"\033]8;;{url}\033\\{url}"
            "\033]8;;;;\033\\\n",
        )
        return 0

    @staticmethod
    def _normalize_remote_row(
        *,
        host: RemoteHost,
        row: dict[str, JsonValue],
    ) -> StatusRow:
        """Normalize one remote status row.

        Args:
            host: Remote host.
            row: Remote row.

        Returns:
            Normalized status row.

        """
        normalized: StatusRow = {}
        for key, value in row.items():
            supported: StatusValue | None = status_value(
                value=value,
            )
            if supported is not None:
                normalized[key] = supported
        normalized["host"] = host.name
        return normalized

    def _option(self, *, args: tuple[str, ...]) -> int:
        """Handle a session-less flag invocation.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        if args[0] == "--exec":
            return self.exec(
                args=args[1:],
                session_id=None,
            )
        if args[0] in ("--send", "--send-key"):
            return self.send(
                args=args[1:],
                session_id=None,
                key_mode=args[0] == "--send-key",
            )
        _ = stderr.write(
            f"Unknown district option: {args[0]}\n",
        )
        return 2

    def _observation_inspection(
        self,
        *,
        args: tuple[str, ...],
        home: Path,
    ) -> int | None:
        """Dispatch qualification, explain, and doctor commands.

        Args:
            args: Args.
            home: Home.

        Returns:
            Exit status when handled, otherwise None.

        """
        if args[:2] == ("test", "integration"):
            return self.integration_test(home=home, args=args[2:])
        if args[:1] == ("--test-integration",):
            return self.integration_test(home=home, args=args[1:])
        if args[:1] in (("explain",), ("--explain",)):
            if len(args[1:]) != 1:
                _ = stderr.write("Usage: district explain SESSION_ID\n")
                return 2
            return self.explain(session_id=args[1])
        if args[:1] in (("--doctor",), ("--test",)):
            return (
                self._doctor(home=home, full="--full" in args[1:])
                if args[0] == "--doctor"
                else self_test(home=home)
            )
        return None

    def _observation_actions(
        self,
        *,
        args: tuple[str, ...],
        home: Path,
    ) -> int | None:
        """Dispatch history, event, purge, and dashboard commands.

        Args:
            args: Args.
            home: Home.

        Returns:
            Exit status when handled, otherwise None.

        """
        if args[:1] == ("--history",):
            return self.history(
                home=home,
                filters=args[1:],
            )
        if args[:1] == ("--events",):
            return self.events(
                home=home,
                filters=args[1:],
            )
        if args[:1] == ("--subscribe",):
            return self.subscribe(
                home=home,
                once="--once" in args[1:],
            )
        if args[:1] == ("--purge",):
            return self.purge(home=home)
        return (
            self._web(args=args[1:])
            if args[:1] == ("--web",)
            else self._watch(args=args)
            if args[:1] in (("--watch",), ("--view",))
            else None
        )

    def _web(self, *, args: tuple[str, ...]) -> int:
        """Manage or run the local FastAPI/WebSocket dashboard.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        home: Path = Path.home()
        dashboard: WebDashboard = WebDashboard(
            service=self.service,
            home=home,
        )
        startup: WebStartup = WebStartup(home=home)
        special: int | None = self.web_special(
            args=args,
            dashboard=dashboard,
            startup=startup,
        )
        if special is not None:
            return special
        qr: bool = "--qr" in args
        options: tuple[str, int, bool, Exposure] | None = self.web_options(
            args=tuple(value for value in args if value != "--qr"),
        )
        if options is None:
            return 2
        host, port, browser, exposure = options
        try:
            return dashboard.run(
                host=host,
                port=port,
                browser=browser and not qr,
                exposure=exposure,
                lan=qr,
            )
        except (ExposureError, PairingUnavailableError) as error:
            _ = stderr.write(f"district --web: {error}\n")
            return 1

    def _watch(self, *, args: tuple[str, ...]) -> int:
        """Render read-only snapshots or the interactive multi-session TUI.

        Args:
            args: Args.

        Returns:
            The resulting value.

        """
        filters: tuple[str, ...] = tuple(
            value.lower() for value in args[1:] if value != "--once"
        )
        if args[:1] == ("--view",):
            return self.view(filters=filters)

        def fetch() -> tuple[SessionEntry, ...]:
            """Fetch dashboard entries.

            Returns:
                The resulting value.

            """
            entries: tuple[
                SessionEntry,
                ...,
            ] = self.service.list_entries()
            if not filters:
                return entries
            return tuple(
                entry
                for entry in entries
                if entry.id in filters or entry.agent.lower() in filters
            )

        dashboard: WatchTui = WatchTui(fetch=fetch)
        if "--once" in args or not stdout.isatty():
            _ = stdout.write(dashboard.once() + "\n")
            return 0
        return dashboard.run()

    def _local_status_rows(self) -> list[StatusRow]:
        """Build local status rows.

        Returns:
            Local status rows.

        """
        rows: list[StatusRow] = []
        for entry in self.service.list_entries():
            snapshot: SessionSnapshot = Monitor.snapshot(
                entry=entry,
            )
            rows.append(
                {
                    "host": entry.hostname or "local",
                    "host_id": entry.host_id,
                    "session_id": entry.id,
                    "session_uuid": entry.uuid,
                    "session": entry.session,
                    "attach_command": entry.command,
                    "initiator": entry.initiator,
                    "agent": entry.agent or entry.initiator,
                    "agent_session": entry.agent_session,
                    "backend": entry.backend,
                    "surface": entry.surface,
                    "editor": entry.editor,
                    "workspace": entry.workspace,
                    "cwd": entry.cwd,
                    "created_at": entry.created_at,
                    "initial_command": entry.initial_command,
                    "pid": entry.pid,
                    "state": (
                        entry.agent_state
                        if entry.agent_state != "IDLE"
                        else snapshot.state
                    ),
                    "project": entry.workspace or entry.cwd,
                    "command": snapshot.current_command,
                    "current_command": snapshot.current_command,
                    "reconnectable": snapshot.reconnectable,
                    "clients": snapshot.clients,
                    "attached": snapshot.clients > 0,
                    "live": snapshot.state != "DEAD",
                    "age_seconds": snapshot.age_seconds,
                },
            )
        return rows


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Run the district terminal CLI.

    Args:
        arguments: Arguments.

    Returns:
        The resulting value.

    """
    return Cli(service=default_service()).run(
        arguments=(tuple(argv[1:]) if arguments is None else arguments),
    )
