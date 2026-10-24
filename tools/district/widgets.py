# Copyright (c) 2026 Project contributors.
"""Typed result widgets that the web console renders instead of raw JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .json_config import JsonValue
type Cell = str | int | float | bool | None


class Tone(StrEnum):
    """Say how a value should be emphasised.

    Attributes:
        neutral: Plain information.
        good: A healthy or successful value.
        warn: Something that deserves attention.
        bad: A failure.

    """

    neutral = "neutral"
    good = "good"
    warn = "warn"
    bad = "bad"


class CellKind(StrEnum):
    """Tell the renderer how to present a table column.

    Attributes:
        text: Ordinary text.
        mono: Monospace text, for commands and paths.
        time: An ISO timestamp, shown relative to now.
        duration: Seconds, shown as a short duration.
        bytes: A byte count, shown with a unit.
        status: A state name, shown as a badge.
        session: A session id, shown as a link to that session.
        agent: An agent name, shown as a link to that agent.
        exit: A process exit code, shown as ok or failed.

    """

    text = "text"
    mono = "mono"
    time = "time"
    duration = "duration"
    bytes = "bytes"
    status = "status"
    session = "session"
    agent = "agent"
    exit = "exit"


@dataclass(frozen=True, slots=True, kw_only=True)
class Metric:
    """One headline number.

    Attributes:
        label: What the number measures.
        value: The number, already formatted.
        detail: Optional secondary line.
        tone: Emphasis.
        percent: Fill for a gauge, 0 to 100, when the value is a share.

    """

    label: str
    value: str
    detail: str = ""
    tone: Tone = Tone.neutral
    percent: float | None = None

    def as_json(self) -> dict[str, JsonValue]:
        """Render the metric.

        Returns:
            A JSON-compatible mapping.

        """
        return {
            "label": self.label,
            "value": self.value,
            "detail": self.detail,
            "tone": self.tone.value,
            "percent": self.percent,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Fact:
    """One labelled value.

    Attributes:
        label: Name of the fact.
        value: The value.
        tone: Emphasis.
        href: Dashboard route the value links to, or empty.

    """

    label: str
    value: str
    tone: Tone = Tone.neutral
    href: str = ""

    def as_json(self) -> dict[str, JsonValue]:
        """Render the fact.

        Returns:
            A JSON-compatible mapping.

        """
        return {
            "label": self.label,
            "value": self.value,
            "tone": self.tone.value,
            "href": self.href,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Column:
    """One table column.

    Attributes:
        key: Row key the column reads.
        label: Column heading.
        kind: How the renderer presents the cells.

    """

    key: str
    label: str
    kind: CellKind = CellKind.text

    def as_json(self) -> dict[str, JsonValue]:
        """Render the column.

        Returns:
            A JSON-compatible mapping.

        """
        return {"key": self.key, "label": self.label, "kind": self.kind.value}


@dataclass(frozen=True, slots=True, kw_only=True)
class Check:
    """One pass or fail line.

    Attributes:
        name: What was checked.
        ok: Whether it passed.
        detail: Why, in a few words.

    """

    name: str
    ok: bool
    detail: str = ""

    def as_json(self) -> dict[str, JsonValue]:
        """Render the check.

        Returns:
            A JSON-compatible mapping.

        """
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True, slots=True, kw_only=True)
class Metrics:
    """A row of headline numbers.

    Attributes:
        items: The metrics, in display order.

    """

    items: tuple[Metric, ...]

    def as_json(self) -> dict[str, JsonValue]:
        """Render the widget.

        Returns:
            A JSON-compatible mapping.

        """
        return {
            "type": "metrics",
            "items": [item.as_json() for item in self.items],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Facts:
    """A titled list of labelled values.

    Attributes:
        title: Heading of the group.
        items: The facts, in display order.

    """

    title: str
    items: tuple[Fact, ...]

    def as_json(self) -> dict[str, JsonValue]:
        """Render the widget.

        Returns:
            A JSON-compatible mapping.

        """
        return {
            "type": "facts",
            "title": self.title,
            "items": [item.as_json() for item in self.items],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Table:
    """A titled table.

    Attributes:
        title: Heading of the table.
        columns: The columns, in display order.
        rows: One mapping of column key to cell per row.
        empty: What to say when there are no rows.

    """

    title: str
    columns: tuple[Column, ...]
    rows: tuple[dict[str, Cell], ...] = field(default=())
    empty: str = "Nothing to show."

    def as_json(self) -> dict[str, JsonValue]:
        """Render the widget.

        Returns:
            A JSON-compatible mapping.

        """
        return {
            "type": "table",
            "title": self.title,
            "columns": [column.as_json() for column in self.columns],
            "rows": [dict(row) for row in self.rows],
            "empty": self.empty,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Checks:
    """A titled list of pass or fail lines.

    Attributes:
        title: Heading of the list.
        items: The checks, in display order.

    """

    title: str
    items: tuple[Check, ...]

    def as_json(self) -> dict[str, JsonValue]:
        """Render the widget.

        Returns:
            A JSON-compatible mapping.

        """
        return {
            "type": "checks",
            "title": self.title,
            "items": [item.as_json() for item in self.items],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Console:
    """A block of terminal output.

    Attributes:
        title: Heading of the block.
        text: The output.

    """

    title: str
    text: str

    def as_json(self) -> dict[str, JsonValue]:
        """Render the widget.

        Returns:
            A JSON-compatible mapping.

        """
        return {"type": "console", "title": self.title, "text": self.text}


@dataclass(frozen=True, slots=True, kw_only=True)
class Notice:
    """A one-line message.

    Attributes:
        tone: Emphasis.
        text: The message.

    """

    tone: Tone
    text: str

    def as_json(self) -> dict[str, JsonValue]:
        """Render the widget.

        Returns:
            A JSON-compatible mapping.

        """
        return {"type": "notice", "tone": self.tone.value, "text": self.text}


type Widget = Metrics | Facts | Table | Checks | Console | Notice


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandResult:
    """What a district command produced, ready to display.

    Attributes:
        command: Name of the command that ran.
        title: Heading for the result.
        ok: Whether the command succeeded.
        widgets: The widgets that make up the result.

    """

    command: str
    title: str
    ok: bool
    widgets: tuple[Widget, ...]

    def as_json(self) -> dict[str, JsonValue]:
        """Render the result.

        Returns:
            A JSON-compatible mapping.

        """
        return {
            "command": self.command,
            "title": self.title,
            "ok": self.ok,
            "widgets": [widget.as_json() for widget in self.widgets],
        }
