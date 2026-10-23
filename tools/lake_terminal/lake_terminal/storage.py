# Copyright (c) 2026 Project contributors.
"""Transactional SQLite state, command, process, and event storage."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from json import dumps
from sqlite3 import Connection, DatabaseError, connect
from typing import TYPE_CHECKING, Final, TypedDict, Unpack, cast

from .models import SessionEntry
from .product import secure_private_file, state_directory

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .events import LakeEvent
    from .privacy import PrivacyPolicy
type SqlValue = str | int | float | None
type SessionRow = tuple[SqlValue, ...]
type EventRow = tuple[str]
type HostRow = tuple[str, str, str, str, int]
type WebServerRow = tuple[int, str, str, int, str]
type CommandRow = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    int | None,
    float,
]


class CommandStartedValues(TypedDict):
    """Keyword values required to persist a command start.

    Attributes:
        command_id: Command identifier.
        session_uuid: Stable session UUID.
        session_id: Display session identifier.
        host_id: Host identifier.
        agent: Agent provider.
        agent_session: Provider session identifier.
        surface: Execution surface.
        editor: Editor identifier.
        command: Executed command.
        cwd: Working directory.
        started_at: Command start timestamp.

    """

    command_id: str
    session_uuid: str
    session_id: str
    host_id: str
    agent: str
    agent_session: str
    surface: str
    editor: str
    command: str
    cwd: str
    started_at: str


class Constants:
    """Database constants.

    Attributes:
        schema_version: Schema version.
        database_name: Database name.
        archive_schema_boundary: Oldest schema that can be upgraded in place.

    """

    schema_version: Final[int] = 7
    database_name: Final[str] = "lake.db"
    archive_schema_boundary: Final[int] = 4


class LakeDatabase:
    """Hold canonical transactional Lake Terminal observability state."""

    def __init__(self, *, home: Path) -> None:
        """Open/create the per-user database and apply SQLite migrations.

        Args:
            home: Home.

        """
        directory: Path = state_directory(home=home)
        directory.mkdir(parents=True, exist_ok=True)
        self.path: Path = directory / Constants.database_name
        self.recovered_from: str = ""
        try:
            self._migrate()
        except DatabaseError:
            current: datetime = datetime.now(tz=UTC)
            stamp: str = current.strftime(format="%Y%m%dT%H%M%SZ")
            corrupt: Path = self.path.with_name(
                name=f"{self.path.name}.corrupt-{stamp}",
            )
            if self.path.exists():
                _ = self.path.replace(target=corrupt)
                self.recovered_from = str(object=corrupt)
            self.path.with_name(
                name=self.path.name + "-wal",
            ).unlink(
                missing_ok=True,
            )
            self.path.with_name(
                name=self.path.name + "-shm",
            ).unlink(
                missing_ok=True,
            )
            self._migrate()
        secure_private_file(path=self.path)

    @classmethod
    def upgrade_sqlite_schema(
        cls,
        *,
        database: Connection,
        version: int,
    ) -> None:
        """Upgrade SQLite state without destroying historical rows.

        Args:
            database: Database.
            version: Version.

        """
        if version >= Constants.schema_version:
            return
        if version >= Constants.archive_schema_boundary:
            cls._create_current_schema(database=database)
            return
        tables: tuple[str, ...] = (
            "sessions",
            "commands",
            "events",
            "processes",
            "hosts",
        )
        for table in tables:
            exists: tuple[int] | None = cast(
                typ="tuple[int] | None",
                val=database.execute(
                    (
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name=?"
                    ),
                    (table,),
                ).fetchone(),
            )
            if exists is None:
                continue
            archived: str = f"{table}_archive_v{version}"
            suffix: int = 1
            while cls.table_exists(
                database=database,
                table=archived,
            ):
                suffix += 1
                archived = f"{table}_archive_v{version}_{suffix}"
            _ = database.execute(
                f'ALTER TABLE "{table}" RENAME TO "{archived}"',
            )
        cls._create_current_schema(database=database)

    @staticmethod
    def table_exists(
        *,
        database: Connection,
        table: str,
    ) -> bool:
        """Return whether a SQLite table exists.

        Args:
            database: Database.
            table: Table name.

        Returns:
            Whether the table exists.

        """
        row: tuple[int] | None = cast(
            typ="tuple[int] | None",
            val=database.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone(),
        )
        return row is not None

    def schema_version(self) -> int:
        """Return the currently installed database schema version.

        Returns:
            The currently installed schema version.

        """
        with closing(thing=self._connection()) as database, database:
            row: tuple[str] | None = cast(
                typ="tuple[str] | None",
                val=database.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'",
                ).fetchone(),
            )
        return int(row[0]) if row is not None else 0

    def set_agent_context(
        self,
        *,
        provider: str,
        session_id: str,
        workspace: str,
    ) -> None:
        """Persist provider session identity without mutating shell commands.

        Args:
            provider: Provider.
            session_id: Session id.
            workspace: Workspace.

        """
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                (
                    "INSERT OR REPLACE INTO agent_contexts("
                    "provider,workspace,session_id,updated_at) "
                    "VALUES(?,?,?,?)"
                ),
                (
                    provider,
                    workspace,
                    session_id,
                    datetime.now(tz=UTC).isoformat(),
                ),
            )

    def agent_context(self, *, workspace: str) -> tuple[str, str] | None:
        """Return the newest out-of-band agent identity for a workspace.

        Args:
            workspace: Workspace.

        Returns:
            Provider and session identifier when available.

        """
        with closing(thing=self._connection()) as database, database:
            row: tuple[str, str] | None = cast(
                typ="tuple[str, str] | None",
                val=database.execute(
                    "SELECT provider,session_id FROM agent_contexts "
                    "WHERE workspace=? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (workspace,),
                ).fetchone(),
            )
        return row

    def sessions(self) -> tuple[SessionEntry, ...]:
        """Return all canonical session rows.

        Returns:
            Canonical sessions.

        """
        with closing(thing=self._connection()) as database, database:
            rows: list[SessionRow] = cast(
                typ="list[SessionRow]",
                val=database.execute(
                    "SELECT id,command,session,created_at,initiator,pid,"
                    "parent_pid,hostname,cwd,shell,term,terminal_program,tty,"
                    "invocation,initial_command,agent,agent_session,backend,"
                    "operating_system,architecture,surface,editor,workspace,"
                    "attribution,reconnectable,current_command,"
                    "last_activity_at,session_uuid,host_id,"
                    "process_started_token,current_command_id,agent_state "
                    "FROM sessions ORDER BY created_at,id",
                ).fetchall(),
            )
        return tuple(self._session_from_row(row=row) for row in rows)

    def replace_sessions(
        self,
        *,
        entries: tuple[SessionEntry, ...],
    ) -> None:
        """Replace all canonical session rows transactionally.

        Args:
            entries: Entries.

        """
        with closing(thing=self._connection()) as database, database:
            self._replace_sessions_on(
                database=database,
                entries=entries,
            )

    def update_sessions(
        self,
        *,
        transform: Callable[
            [tuple[SessionEntry, ...]],
            tuple[SessionEntry, ...],
        ],
    ) -> tuple[SessionEntry, ...]:
        """Atomically read, transform, and persist all session rows.

        Args:
            transform: Transform.

        Returns:
            Updated sessions.

        """
        with closing(thing=self._connection()) as database:
            _ = database.execute("BEGIN IMMEDIATE")
            rows: list[SessionRow] = cast(
                typ="list[SessionRow]",
                val=database.execute(
                    "SELECT id,command,session,created_at,initiator,pid,"
                    "parent_pid,hostname,cwd,shell,term,terminal_program,tty,"
                    "invocation,initial_command,agent,agent_session,backend,"
                    "operating_system,architecture,surface,editor,workspace,"
                    "attribution,reconnectable,current_command,"
                    "last_activity_at,session_uuid,host_id,"
                    "process_started_token,current_command_id,agent_state "
                    "FROM sessions ORDER BY created_at,id",
                ).fetchall(),
            )
            current: tuple[SessionEntry, ...] = tuple(
                self._session_from_row(row=row) for row in rows
            )
            updated: tuple[SessionEntry, ...] = transform(current)
            if updated != current:
                self._replace_sessions_on(
                    database=database,
                    entries=updated,
                )
            database.commit()
        return updated

    def append_event(self, *, event: LakeEvent) -> None:
        """Persist one normalized event.

        Args:
            event: Event.

        """
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                "INSERT OR REPLACE INTO events("
                "event_id,event_type,occurred_at,host_id,session_uuid,"
                "session_id,command_id,agent,state,payload) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    event.id,
                    event.event_type,
                    event.occurred_at,
                    event.host_id,
                    event.session_uuid,
                    event.session_id,
                    event.command_id,
                    event.agent,
                    event.state,
                    event.json(),
                ),
            )

    def event_json(
        self,
        *,
        filters: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        """Return persisted event JSON matching textual filters.

        Args:
            filters: Filters.

        Returns:
            Matching serialized events.

        """
        with closing(thing=self._connection()) as database, database:
            rows: list[EventRow] = cast(
                typ="list[EventRow]",
                val=database.execute(
                    "SELECT payload FROM events ORDER BY occurred_at,event_id",
                ).fetchall(),
            )
        lowered: tuple[str, ...] = tuple(value.lower() for value in filters)
        return tuple(
            row[0]
            for row in rows
            if not lowered or any(value in row[0].lower() for value in lowered)
        )

    def command_started(
        self,
        **values: Unpack[CommandStartedValues],
    ) -> None:
        """Persist the authoritative start of a command.

        Args:
            **values: Typed command-start fields.

        """
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                "INSERT OR REPLACE INTO commands("
                "command_id,session_uuid,session_id,host_id,agent,"
                "agent_session,surface,editor,command,cwd,started_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    values["command_id"],
                    values["session_uuid"],
                    values["session_id"],
                    values["host_id"],
                    values["agent"],
                    values["agent_session"],
                    values["surface"],
                    values["editor"],
                    values["command"],
                    values["cwd"],
                    values["started_at"],
                ),
            )

    def observe_processes(
        self,
        *,
        session_uuid: str,
        processes: tuple[tuple[int, str, str], ...],
    ) -> None:
        """Replace observed process relationships for one session.

        Args:
            session_uuid: Session uuid.
            processes: Processes.

        """
        now: str = datetime.now(tz=UTC).isoformat()
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                "DELETE FROM processes WHERE session_uuid=?",
                (session_uuid,),
            )
            _ = database.executemany(
                "INSERT INTO processes("
                "session_uuid,pid,started_token,command_id,observed_at) "
                "VALUES(?,?,?,?,?)",
                (
                    (
                        session_uuid,
                        pid,
                        token,
                        command_id,
                        now,
                    )
                    for pid, token, command_id in processes
                ),
            )

    def process_count(self, *, session_uuid: str) -> int:
        """Return the number of currently recorded descendants.

        Args:
            session_uuid: Session uuid.

        Returns:
            Recorded process count.

        """
        with closing(thing=self._connection()) as database, database:
            row: tuple[int] | None = cast(
                typ="tuple[int] | None",
                val=database.execute(
                    "SELECT COUNT(*) FROM processes WHERE session_uuid=?",
                    (session_uuid,),
                ).fetchone(),
            )
        return row[0] if row is not None else 0

    def command_started_at(self, *, command_id: str) -> str:
        """Return the persisted start time for one command.

        Args:
            command_id: Command id.

        Returns:
            Persisted start time, or an empty string when absent.

        """
        with closing(thing=self._connection()) as database, database:
            row: tuple[str] | None = cast(
                typ="tuple[str] | None",
                val=database.execute(
                    "SELECT started_at FROM commands WHERE command_id=?",
                    (command_id,),
                ).fetchone(),
            )
        return row[0] if row is not None else ""

    def command_finished(
        self,
        *,
        command_id: str,
        finished_at: str,
        exit_code: int,
        duration_seconds: float,
    ) -> None:
        """Persist authoritative command completion.

        Args:
            command_id: Command id.
            finished_at: Finished at.
            exit_code: Exit code.
            duration_seconds: Duration seconds.

        """
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                "UPDATE commands SET finished_at=?,exit_code=?,"
                "duration_seconds=? WHERE command_id=?",
                (
                    finished_at,
                    exit_code,
                    duration_seconds,
                    command_id,
                ),
            )

    def command_json(self) -> tuple[str, ...]:
        """Return command history as compact JSON records.

        Returns:
            Serialized command history.

        """
        with closing(thing=self._connection()) as database, database:
            rows: list[CommandRow] = cast(
                typ="list[CommandRow]",
                val=database.execute(
                    "SELECT command_id,session_uuid,session_id,host_id,"
                    "agent,agent_session,surface,editor,command,cwd,"
                    "started_at,finished_at,exit_code,duration_seconds "
                    "FROM commands ORDER BY started_at,command_id",
                ).fetchall(),
            )
        return tuple(
            dumps(
                obj={
                    "command_id": row[0],
                    "session_uuid": row[1],
                    "session_id": row[2],
                    "host_id": row[3],
                    "agent": row[4],
                    "agent_session": row[5],
                    "surface": row[6],
                    "editor": row[7],
                    "command": row[8],
                    "cwd": row[9],
                    "started_at": row[10],
                    "finished_at": row[11],
                    "exit_code": row[12],
                    "duration_seconds": row[13],
                },
                separators=(",", ":"),
            )
            for row in rows
        )

    def host_rows(self) -> tuple[tuple[str, str, str, bool], ...]:
        """Return configured remote execution hosts.

        Returns:
            Configured host summaries.

        """
        with closing(thing=self._connection()) as database, database:
            rows: list[HostRow] = cast(
                typ="list[HostRow]",
                val=database.execute(
                    "SELECT host_id,name,target,command,enabled "
                    "FROM hosts ORDER BY name",
                ).fetchall(),
            )
        return tuple(
            (
                row[0],
                row[1],
                row[2],
                bool(row[4]),
            )
            for row in rows
        )

    def host_details(
        self,
    ) -> tuple[tuple[str, str, str, str, bool], ...]:
        """Return complete configured remote host rows.

        Returns:
            Complete configured host rows.

        """
        with closing(thing=self._connection()) as database, database:
            rows: list[HostRow] = cast(
                typ="list[HostRow]",
                val=database.execute(
                    (
                        "SELECT host_id,name,target,command,enabled "
                        "FROM hosts ORDER BY name"
                    ),
                ).fetchall(),
            )
        return tuple(
            (
                row[0],
                row[1],
                row[2],
                row[3],
                bool(row[4]),
            )
            for row in rows
        )

    def upsert_host(
        self,
        *,
        host_id: str,
        name: str,
        target: str,
        command: str,
        enabled: bool,
    ) -> None:
        """Create or replace one remote-host configuration row.

        Args:
            host_id: Host id.
            name: Name.
            target: Target.
            command: Command.
            enabled: Enabled.

        """
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                (
                    "INSERT INTO hosts("
                    "host_id,name,target,command,enabled,updated_at) "
                    "VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "host_id=excluded.host_id,"
                    "target=excluded.target,"
                    "command=excluded.command,"
                    "enabled=excluded.enabled,"
                    "updated_at=excluded.updated_at"
                ),
                (
                    host_id,
                    name,
                    target,
                    command,
                    1 if enabled else 0,
                    datetime.now(tz=UTC).isoformat(),
                ),
            )

    def remove_host(self, *, name: str) -> None:
        """Delete one remote-host configuration row by name.

        Args:
            name: Name.

        """
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                "DELETE FROM hosts WHERE name=?",
                (name,),
            )

    def purge(self, *, policy: PrivacyPolicy) -> tuple[int, int]:
        """Delete expired command and event metadata.

        Args:
            policy: Policy.

        Returns:
            Deleted command and event counts.

        """
        now: datetime = datetime.now(tz=UTC)
        command_cutoff: str = (
            now - timedelta(days=policy.history_days)
        ).isoformat()
        event_cutoff: str = (
            now - timedelta(days=policy.event_days)
        ).isoformat()
        with closing(thing=self._connection()) as database, database:
            commands: int = database.execute(
                "DELETE FROM commands WHERE started_at < ?",
                (command_cutoff,),
            ).rowcount
            events: int = database.execute(
                "DELETE FROM events WHERE occurred_at < ?",
                (event_cutoff,),
            ).rowcount
        return commands, events

    def row_counts(self) -> tuple[int, int, int, int]:
        """Return session, command, event, and remote-host row counts.

        Returns:
            Session, command, event, and host counts.

        """
        with closing(thing=self._connection()) as database, database:
            sessions: int = self._count(
                database=database,
                table="sessions",
            )
            commands: int = self._count(
                database=database,
                table="commands",
            )
            events: int = self._count(
                database=database,
                table="events",
            )
            hosts: int = self._count(
                database=database,
                table="hosts",
            )
        return sessions, commands, events, hosts

    def set_web_server(
        self,
        *,
        pid: int,
        started_token: str,
        host: str,
        port: int,
        url: str,
    ) -> None:
        """Persist the singleton web-server identity.

        Args:
            pid: Pid.
            started_token: Started token.
            host: Host.
            port: Port.
            url: Url.

        """
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                "INSERT OR REPLACE INTO web_server("
                "singleton,pid,started_token,host,port,url,updated_at) "
                "VALUES(1,?,?,?,?,?,?)",
                (
                    pid,
                    started_token,
                    host,
                    port,
                    url,
                    datetime.now(tz=UTC).isoformat(),
                ),
            )

    def web_server(self) -> WebServerRow | None:
        """Return the persisted singleton web-server identity.

        Returns:
            Persisted web-server identity when available.

        """
        with closing(thing=self._connection()) as database, database:
            row: WebServerRow | None = cast(
                typ="WebServerRow | None",
                val=database.execute(
                    "SELECT pid,started_token,host,port,url "
                    "FROM web_server WHERE singleton=1",
                ).fetchone(),
            )
        return row

    def clear_web_server(self) -> None:
        """Clear the singleton web-server identity."""
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                "DELETE FROM web_server WHERE singleton=1",
            )

    def integrity(self) -> str:
        """Run SQLite integrity checking.

        Returns:
            SQLite integrity result.

        """
        with closing(thing=self._connection()) as database, database:
            row: tuple[SqlValue] | None = cast(
                typ="tuple[SqlValue] | None",
                val=database.execute(
                    "PRAGMA integrity_check",
                ).fetchone(),
            )
        if row is None:
            return "missing"
        return str(object=row[0])

    @classmethod
    def _replace_sessions_on(
        cls,
        *,
        database: Connection,
        entries: tuple[SessionEntry, ...],
    ) -> None:
        """Replace session rows on an existing transaction.

        Args:
            database: Database.
            entries: Entries.

        """
        now: str = datetime.now(tz=UTC).isoformat()
        _ = database.execute("DELETE FROM sessions")
        _ = database.executemany(
            (
                "INSERT INTO sessions("
                "id,command,session,created_at,initiator,pid,parent_pid,"
                "hostname,cwd,shell,term,terminal_program,tty,invocation,"
                "initial_command,agent,agent_session,backend,operating_system,"
                "architecture,surface,editor,workspace,attribution,"
                "reconnectable,current_command,last_activity_at,session_uuid,"
                "host_id,process_started_token,current_command_id,agent_state,"
                "updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "?,?,?,?,?,?)"
            ),
            (
                cls._session_values(
                    entry=entry,
                    updated_at=now,
                )
                for entry in entries
            ),
        )

    @staticmethod
    def _count(*, database: Connection, table: str) -> int:
        """Return the row count for one trusted internal table.

        Args:
            database: Database connection.
            table: Trusted internal table name.

        Returns:
            Number of rows.

        """
        queries: Final[dict[str, str]] = {
            "sessions": "SELECT COUNT(*) FROM sessions",
            "commands": "SELECT COUNT(*) FROM commands",
            "events": "SELECT COUNT(*) FROM events",
            "hosts": "SELECT COUNT(*) FROM hosts",
        }
        query: str = queries[table]
        row: tuple[int] = cast(
            typ="tuple[int]",
            val=database.execute(query).fetchone(),
        )
        return row[0]

    @staticmethod
    def _create_current_schema(*, database: Connection) -> None:
        """Create the current normalized schema in a new database.

        Args:
            database: Database.

        """
        _ = database.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                session TEXT NOT NULL,
                created_at TEXT NOT NULL,
                initiator TEXT NOT NULL,
                pid INTEGER NOT NULL,
                parent_pid INTEGER NOT NULL,
                hostname TEXT NOT NULL,
                cwd TEXT NOT NULL,
                shell TEXT NOT NULL,
                term TEXT NOT NULL,
                terminal_program TEXT NOT NULL,
                tty TEXT NOT NULL,
                invocation TEXT NOT NULL,
                initial_command TEXT NOT NULL,
                agent TEXT NOT NULL,
                agent_session TEXT NOT NULL,
                backend TEXT NOT NULL,
                operating_system TEXT NOT NULL,
                architecture TEXT NOT NULL,
                surface TEXT NOT NULL,
                editor TEXT NOT NULL,
                workspace TEXT NOT NULL,
                attribution TEXT NOT NULL,
                reconnectable INTEGER NOT NULL,
                current_command TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                session_uuid TEXT NOT NULL,
                host_id TEXT NOT NULL,
                process_started_token TEXT NOT NULL,
                current_command_id TEXT NOT NULL,
                agent_state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY,
                session_uuid TEXT NOT NULL,
                session_id TEXT NOT NULL,
                host_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                agent_session TEXT NOT NULL,
                surface TEXT NOT NULL,
                editor TEXT NOT NULL,
                command TEXT NOT NULL,
                cwd TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                exit_code INTEGER,
                duration_seconds REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                host_id TEXT NOT NULL,
                session_uuid TEXT NOT NULL,
                session_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                state TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processes (
                session_uuid TEXT NOT NULL,
                pid INTEGER NOT NULL,
                started_token TEXT NOT NULL,
                command_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(session_uuid, pid, started_token)
            );
            CREATE TABLE IF NOT EXISTS agent_contexts (
                provider TEXT NOT NULL,
                workspace TEXT NOT NULL,
                session_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(provider, workspace)
            );
            CREATE INDEX IF NOT EXISTS agent_contexts_workspace_idx
            ON agent_contexts(workspace, updated_at);
            CREATE TABLE IF NOT EXISTS hosts (
                host_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                target TEXT NOT NULL,
                command TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_server (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                pid INTEGER NOT NULL,
                started_token TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                url TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS sessions_uuid_unique_idx
            ON sessions(session_uuid) WHERE session_uuid <> '';
            CREATE INDEX IF NOT EXISTS sessions_host_idx
            ON sessions(host_id, created_at);
            CREATE INDEX IF NOT EXISTS sessions_agent_idx
            ON sessions(agent, created_at);
            CREATE INDEX IF NOT EXISTS events_session_idx
            ON events(session_uuid, occurred_at);
            CREATE INDEX IF NOT EXISTS events_agent_idx
            ON events(agent, occurred_at);
            CREATE INDEX IF NOT EXISTS commands_session_idx
            ON commands(session_uuid, started_at);
            """,
        )

    @staticmethod
    def _session_values(
        *,
        entry: SessionEntry,
        updated_at: str,
    ) -> tuple[SqlValue, ...]:
        """Return one session row in SQLite column order.

        Args:
            entry: Entry.
            updated_at: Updated at.

        Returns:
            SQLite row values.

        """
        return (
            entry.id,
            entry.command,
            entry.session,
            entry.created_at,
            entry.initiator,
            entry.pid,
            entry.parent_pid,
            entry.hostname,
            entry.cwd,
            entry.shell_location,
            entry.term,
            entry.terminal_program,
            entry.tty,
            entry.invocation,
            entry.initial_command,
            entry.agent,
            entry.agent_session,
            entry.backend,
            entry.operating_system,
            entry.architecture,
            entry.surface,
            entry.editor,
            entry.workspace,
            entry.attribution,
            1 if entry.reconnectable else 0,
            entry.current_command,
            entry.last_activity_at,
            entry.uuid,
            entry.host_id,
            entry.process_started_token,
            entry.current_command_id,
            entry.agent_state,
            updated_at,
        )

    @staticmethod
    def _session_from_row(*, row: SessionRow) -> SessionEntry:
        """Construct a session model from a normalized SQLite row.

        Args:
            row: Row.

        Returns:
            Session model.

        """
        return SessionEntry(
            id=str(object=row[0] or ""),
            command=str(object=row[1] or ""),
            session=str(object=row[2] or ""),
            created_at=str(object=row[3] or ""),
            initiator=str(object=row[4] or ""),
            pid=int(row[5] or 0),
            parent_pid=int(row[6] or 0),
            hostname=str(object=row[7] or ""),
            cwd=str(object=row[8] or ""),
            shell_location=str(object=row[9] or ""),
            term=str(object=row[10] or ""),
            terminal_program=str(object=row[11] or ""),
            tty=str(object=row[12] or ""),
            invocation=str(object=row[13] or ""),
            initial_command=str(object=row[14] or ""),
            agent=str(object=row[15] or ""),
            agent_session=str(object=row[16] or ""),
            backend=str(object=row[17] or ""),
            operating_system=str(object=row[18] or ""),
            architecture=str(object=row[19] or ""),
            surface=str(object=row[20] or ""),
            editor=str(object=row[21] or ""),
            workspace=str(object=row[22] or ""),
            attribution=str(object=row[23] or ""),
            reconnectable=bool(row[24]),
            current_command=str(object=row[25] or ""),
            last_activity_at=str(object=row[26] or ""),
            uuid=str(object=row[27] or ""),
            host_id=str(object=row[28] or ""),
            process_started_token=str(object=row[29] or ""),
            current_command_id=str(object=row[30] or ""),
            agent_state=str(object=row[31] or "IDLE"),
        )

    def _connection(self) -> Connection:
        """Return a configured SQLite connection.

        Returns:
            Configured SQLite connection.

        """
        connection: Connection = connect(
            database=self.path,
            timeout=10.0,
        )
        try:
            _ = connection.execute("PRAGMA journal_mode=WAL")
            _ = connection.execute("PRAGMA foreign_keys=ON")
            _ = connection.execute("PRAGMA busy_timeout=10000")
        except DatabaseError:
            connection.close()
            raise
        return connection

    def _migrate(self) -> None:
        """Apply SQLite schema migrations transactionally."""
        with closing(thing=self._connection()) as database, database:
            _ = database.execute(
                (
                    "CREATE TABLE IF NOT EXISTS schema_meta "
                    "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                ),
            )
            row: tuple[str] | None = cast(
                typ="tuple[str] | None",
                val=database.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'",
                ).fetchone(),
            )
            version: int = int(row[0]) if row is not None else 0
            if version < 1:
                self._create_current_schema(database=database)
                version = Constants.schema_version
            elif version < Constants.schema_version:
                self.upgrade_sqlite_schema(
                    database=database,
                    version=version,
                )
                version = Constants.schema_version
            _ = database.execute(
                (
                    "INSERT OR REPLACE INTO schema_meta(key,value) "
                    "VALUES('schema_version',?)"
                ),
                (str(object=version),),
            )
