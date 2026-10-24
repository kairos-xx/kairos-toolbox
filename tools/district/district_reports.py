# Copyright (c) 2026 Project contributors.
"""Turn district data into display widgets; no input or output happens here."""

from __future__ import annotations

from json import loads
from typing import TYPE_CHECKING, Final, cast, final

from .widgets import (
    Cell,
    CellKind,
    Check,
    Checks,
    Column,
    CommandResult,
    Fact,
    Facts,
    Metric,
    Metrics,
    Table,
    Tone,
    Widget,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .json_config import JsonValue
    from .models import SessionEntry


@final
class ReportConstants:
    """Group report constants.

    Attributes:
        agent_initiator: Initiator recorded for agent-created sessions.
        general: Title of the group that holds top-level settings.
        empty: Text shown for a missing value.

    """

    agent_initiator: Final[str] = "agent"
    general: Final[str] = "General"
    empty: Final[str] = "—"


@final
class DistrictReports:
    """Build the widgets that present each read-only district command."""

    @classmethod
    def sessions(
        cls,
        *,
        entries: tuple[SessionEntry, ...],
        kind: str,
        text: str,
    ) -> CommandResult:
        """Present the sessions, split by who runs them.

        Args:
            entries: Every live district.
            kind: ``all``, ``agents`` or ``own``.
            text: Words that must all appear in a row; empty for no filter.

        Returns:
            The result.

        """
        words: list[str] = text.lower().split()
        shown: list[SessionEntry] = [
            entry
            for entry in entries
            if (
                kind == "all"
                or (kind == "agents") == cls.agent_run(entry=entry)
            )
            and all(
                word
                in (
                    f"{entry.id} {entry.agent} {entry.cwd} "
                    f"{entry.current_command} {entry.command}"
                ).lower()
                for word in words
            )
        ]
        agent_run: int = sum(cls.agent_run(entry=entry) for entry in entries)
        return CommandResult(
            command="sessions",
            title="Districts",
            ok=True,
            widgets=(
                Metrics(
                    items=(
                        Metric(
                            label="Districts",
                            value=str(object=len(entries)),
                            detail=f"{len(shown)} shown",
                        ),
                        Metric(
                            label="Agent-run",
                            value=str(object=agent_run),
                        ),
                        Metric(
                            label="Your own",
                            value=str(object=len(entries) - agent_run),
                        ),
                    ),
                ),
                Table(
                    title="Districts",
                    columns=(
                        Column(
                            key="id",
                            label="District",
                            kind=CellKind.session,
                        ),
                        Column(
                            key="agent",
                            label="Agent",
                            kind=CellKind.agent,
                        ),
                        Column(
                            key="state",
                            label="State",
                            kind=CellKind.status,
                        ),
                        Column(
                            key="command",
                            label="Running",
                            kind=CellKind.mono,
                        ),
                        Column(
                            key="cwd",
                            label="Directory",
                            kind=CellKind.mono,
                        ),
                        Column(
                            key="created_at",
                            label="Started",
                            kind=CellKind.time,
                        ),
                    ),
                    rows=tuple(
                        {
                            "id": entry.id,
                            "agent": entry.agent,
                            "state": entry.agent_state,
                            "command": entry.current_command
                            or entry.initial_command,
                            "cwd": entry.cwd,
                            "created_at": entry.created_at,
                        }
                        for entry in shown
                    ),
                    empty="No district matches. Create one with New.",
                ),
            ),
        )

    @classmethod
    def history(
        cls,
        *,
        rows: tuple[str, ...],
        text: str,
        limit: int,
    ) -> CommandResult:
        """Present the newest recorded commands.

        Args:
            rows: Command rows as JSON lines.
            text: Words that must all appear in a row; empty for no filter.
            limit: Most rows to show.

        Returns:
            The result.

        """
        parsed: list[dict[str, Cell]] = cls._newest(
            rows=rows,
            text=text,
            limit=limit,
            order="started_at",
        )
        failed: int = sum(
            1 for row in parsed if row.get("exit_code") not in {0, None}
        )
        return CommandResult(
            command="history",
            title="Command history",
            ok=True,
            widgets=(
                Metrics(
                    items=(
                        Metric(label="Shown", value=str(object=len(parsed))),
                        Metric(
                            label="Failed",
                            value=str(object=failed),
                            tone=Tone.bad if failed else Tone.good,
                        ),
                    ),
                ),
                Table(
                    title="Commands",
                    columns=(
                        Column(
                            key="started_at",
                            label="When",
                            kind=CellKind.time,
                        ),
                        Column(
                            key="session_id",
                            label="District",
                            kind=CellKind.session,
                        ),
                        Column(
                            key="agent",
                            label="Agent",
                            kind=CellKind.agent,
                        ),
                        Column(
                            key="command",
                            label="Command",
                            kind=CellKind.mono,
                        ),
                        Column(
                            key="exit_code",
                            label="Result",
                            kind=CellKind.exit,
                        ),
                        Column(
                            key="duration_seconds",
                            label="Took",
                            kind=CellKind.duration,
                        ),
                    ),
                    rows=tuple(parsed),
                    empty="No commands recorded yet.",
                ),
            ),
        )

    @classmethod
    def events(
        cls,
        *,
        rows: tuple[str, ...],
        limit: int,
    ) -> CommandResult:
        """Present the newest recorded events.

        Args:
            rows: Event rows as JSON lines, already filtered.
            limit: Most rows to show.

        Returns:
            The result.

        """
        return CommandResult(
            command="events",
            title="Activity",
            ok=True,
            widgets=(
                Table(
                    title="Events",
                    columns=(
                        Column(
                            key="occurred_at",
                            label="When",
                            kind=CellKind.time,
                        ),
                        Column(
                            key="event_type",
                            label="Event",
                            kind=CellKind.status,
                        ),
                        Column(
                            key="agent",
                            label="Agent",
                            kind=CellKind.agent,
                        ),
                        Column(
                            key="session_id",
                            label="District",
                            kind=CellKind.session,
                        ),
                        Column(
                            key="command",
                            label="Command",
                            kind=CellKind.mono,
                        ),
                    ),
                    rows=tuple(
                        cls._newest(
                            rows=rows,
                            text="",
                            limit=limit,
                            order="occurred_at",
                        ),
                    ),
                    empty="No activity recorded yet.",
                ),
            ),
        )

    @classmethod
    def settings(cls, *, document: Mapping[str, JsonValue]) -> CommandResult:
        """Present the effective settings as titled groups.

        Args:
            document: The settings report.

        Returns:
            The result.

        """
        general: list[Fact] = []
        groups: list[Widget] = []
        for key, value in document.items():
            if isinstance(value, dict):
                groups.append(
                    Facts(
                        title=key.replace("_", " ").capitalize(),
                        items=tuple(
                            Fact(label=name, value=cls._text(value=item))
                            for name, item in value.items()
                        ),
                    ),
                )
            else:
                general.append(Fact(label=key, value=cls._text(value=value)))
        return CommandResult(
            command="settings",
            title="Settings",
            ok=True,
            widgets=(
                Facts(title=ReportConstants.general, items=tuple(general)),
                *groups,
            ),
        )

    @staticmethod
    def agent_run(*, entry: SessionEntry) -> bool:
        """Say whether an agent, rather than a person, owns a district.

        Args:
            entry: Session entry.

        Returns:
            True for a district created by or attributed to an agent.

        """
        return entry.initiator == ReportConstants.agent_initiator or bool(
            entry.agent,
        )

    @staticmethod
    def health(*, report: Mapping[str, JsonValue]) -> CommandResult:
        """Present the installation self-test.

        Args:
            report: The self-test report.

        Returns:
            The result.

        """
        items: list[JsonValue] = cast(
            typ="list[JsonValue]",
            val=report["checks"],
        )
        checks: tuple[Check, ...] = tuple(
            Check(
                name=str(object=item["name"]),
                ok=bool(item["ok"]),
                detail=str(object=item["detail"]),
            )
            for item in cast(typ="list[dict[str, JsonValue]]", val=items)
        )
        failed: int = sum(not check.ok for check in checks)
        return CommandResult(
            command="health",
            title="Health check",
            ok=failed == 0,
            widgets=(
                Metrics(
                    items=(
                        Metric(
                            label="Passed",
                            value=str(object=len(checks) - failed),
                            tone=Tone.good,
                        ),
                        Metric(
                            label="Failed",
                            value=str(object=failed),
                            tone=Tone.bad if failed else Tone.good,
                        ),
                    ),
                ),
                Checks(title="Checks", items=checks),
            ),
        )

    @staticmethod
    def storage(
        *,
        counts: tuple[int, int, int, int],
        integrity: str,
        schema_version: int,
        database_bytes: int,
    ) -> CommandResult:
        """Present what the district database holds.

        Args:
            counts: Session, command, event and host row counts.
            integrity: The database integrity result.
            schema_version: Version of the schema.
            database_bytes: Size of the database file.

        Returns:
            The result.

        """
        sessions, commands, events, hosts = counts
        return CommandResult(
            command="storage",
            title="Storage",
            ok=integrity == "ok",
            widgets=(
                Metrics(
                    items=(
                        Metric(label="Districts", value=str(object=sessions)),
                        Metric(label="Commands", value=str(object=commands)),
                        Metric(label="Events", value=str(object=events)),
                        Metric(label="Hosts", value=str(object=hosts)),
                    ),
                ),
                Facts(
                    title="Database",
                    items=(
                        Fact(
                            label="Integrity",
                            value=integrity,
                            tone=(
                                Tone.good if integrity == "ok" else Tone.bad
                            ),
                        ),
                        Fact(
                            label="Schema version",
                            value=str(object=schema_version),
                        ),
                        Fact(label="Size", value=f"{database_bytes} bytes"),
                    ),
                ),
            ),
        )

    @staticmethod
    def hosts(
        *,
        rows: tuple[tuple[str, str, str, bool], ...],
    ) -> CommandResult:
        """Present the configured remote hosts.

        Args:
            rows: Name, target, command and enabled flag of each host.

        Returns:
            The result.

        """
        return CommandResult(
            command="hosts",
            title="Remote hosts",
            ok=True,
            widgets=(
                Table(
                    title="Hosts",
                    columns=(
                        Column(key="name", label="Name"),
                        Column(
                            key="target",
                            label="Target",
                            kind=CellKind.mono,
                        ),
                        Column(
                            key="command",
                            label="Command",
                            kind=CellKind.mono,
                        ),
                        Column(
                            key="status",
                            label="Status",
                            kind=CellKind.status,
                        ),
                    ),
                    rows=tuple(
                        {
                            "name": name,
                            "target": target,
                            "command": command,
                            "status": "enabled" if enabled else "disabled",
                        }
                        for name, target, command, enabled in rows
                    ),
                    empty="No remote hosts are configured.",
                ),
            ),
        )

    @classmethod
    def _text(cls, *, value: JsonValue) -> str:
        """Show one settings value as short text.

        Args:
            value: The value.

        Returns:
            Readable text.

        """
        if value is None:
            return ReportConstants.empty
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, list):
            return ", ".join(cls._text(value=item) for item in value) or "none"
        if isinstance(value, dict):
            return ", ".join(
                f"{name}: {cls._text(value=item)}"
                for name, item in value.items()
            )
        return str(object=value)

    @staticmethod
    def _newest(
        *,
        rows: tuple[str, ...],
        text: str,
        limit: int,
        order: str,
    ) -> list[dict[str, Cell]]:
        """Pick the newest rows that contain every word of ``text``.

        Args:
            rows: Rows as JSON lines.
            text: Words that must all appear; empty for no filter.
            limit: Most rows to return.
            order: Key holding the timestamp rows are ordered by.

        Returns:
            Parsed rows, newest first.

        """
        words: list[str] = text.lower().split()
        parsed: list[dict[str, Cell]] = [
            cast(typ="dict[str, Cell]", val=loads(s=row))
            for row in rows
            if all(word in row.lower() for word in words)
        ]
        parsed.sort(
            key=lambda row: str(object=row.get(order) or ""),
            reverse=True,
        )
        return parsed[:limit]
