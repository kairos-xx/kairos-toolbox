# Copyright (c) 2026 Project contributors.
"""Track and display build progress reported by VM and container guests.

Guests stay thin: a worker only announces which planned step it started and
forwards the raw output of that step. All interpretation happens here, on the
host, so one parser serves the PowerShell worker, the POSIX worker and the
Docker stream.

Pip reports exact byte counts for downloads (``--progress-bar raw``), but
nothing measurable for resolving, building wheels, freezing or packaging, so
the overall percentage advances by weighted *steps* rather than by invented
byte fractions; the byte-accurate download is shown as detail beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError, loads
from re import IGNORECASE, Match, Pattern
from re import compile as compile_pattern
from shutil import get_terminal_size
from sys import stdout as sys_stdout
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Final, TextIO, cast, final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
type ProgressSink = Callable[[BuildProgress], None]


@final
class ProgressConstants:
    """Group progress-protocol constants.

    Attributes:
        step_marker: Prefix of the line a shell guest prints per step.
        pip_bar: Matches pip's ``--progress-bar raw`` line.
        pip_download: Matches pip's ``Downloading`` line.
        detail_limit: Longest detail text kept per update.
        refresh_seconds: Shortest interval between live redraws.
        bar_width: Cells in the rendered bar.
        unit_step: Bytes per larger size unit.

    """

    step_marker: Final[str] = "@@district-step@@"
    pip_bar: Final[Pattern[str]] = compile_pattern(
        pattern=r"^Progress\s+(?P<done>\d+)\s+of\s+(?P<total>\d+)\s*$",
    )
    pip_download: Final[Pattern[str]] = compile_pattern(
        pattern=r"^\s*Downloading\s+(?P<ref>\S+)",
        flags=IGNORECASE,
    )
    detail_limit: Final[int] = 200
    refresh_seconds: Final[float] = 0.1
    bar_width: Final[int] = 24
    unit_step: Final[float] = 1000.0


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildProgress:
    """Describe one observable progress update.

    Attributes:
        percent: Weighted share of planned steps already finished; never
            100 until the build itself completes.
        step: One-based index of the running step; ``0`` before the first.
        total_steps: Number of planned steps.
        message: Text of the running step.
        package: Artifact pip is downloading, if any.
        downloaded_bytes: Bytes of ``package`` received so far.
        total_bytes: Size of ``package`` when pip reported it.
        detail: Most recent output line of the running step.
        complete: Whether the build finished.

    """

    percent: float
    step: int
    total_steps: int
    message: str
    package: str | None = None
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    detail: str = ""
    complete: bool = False

    @property
    def download_percent(self) -> float | None:
        """Return byte-accurate progress of the current download.

        Returns:
            Percent of ``package`` received, or ``None`` when unmeasurable.

        """
        if self.package is None or not self.total_bytes:
            return None
        return min(100.0, self.downloaded_bytes / self.total_bytes * 100.0)


@final
class StepMarker:
    """Encode and decode the step line a shell guest prints."""

    @staticmethod
    def parse(*, line: str) -> tuple[int, str] | None:
        """Decode a step line.

        Args:
            line: One output line.

        Returns:
            ``(index, message)``, or ``None`` when ``line`` is no marker.

        """
        prefix: str = ProgressConstants.step_marker
        if not line.startswith(prefix):
            return None
        index, _, message = line.removeprefix(prefix).strip().partition(" ")
        return (int(index), message.strip()) if index.isdecimal() else None

    @staticmethod
    def shell(*, index: int, message: str) -> str:
        """Compose the shell command that prints a step line.

        Args:
            index: One-based step index.
            message: Step text; must not contain a single quote.

        Returns:
            A POSIX ``printf`` command.

        """
        marker: str = f"{ProgressConstants.step_marker} {index} {message}"
        return f"printf '%s\\n' '{marker}'"

    @staticmethod
    def event(*, body: str) -> tuple[int, str] | None:
        """Decode the JSON step event a pull-worker posts.

        Args:
            body: Request body; plain text is not an event.

        Returns:
            ``(index, message)``, or ``None`` when ``body`` is no event.

        """
        if not body.startswith("{"):
            return None
        try:
            document: object = loads(s=body)
        except JSONDecodeError:
            return None
        if not isinstance(document, dict):
            return None
        mapping: dict[str, object] = cast(
            typ="dict[str, object]",
            val=document,
        )
        index: object = mapping.get("step")
        message: object = mapping.get("message", "")
        if not isinstance(index, int) or not isinstance(message, str):
            return None
        return index, message


@final
class PipOutput:
    """Interpret pip's ``--progress-bar raw`` output line by line."""

    @staticmethod
    def bar(*, line: str) -> tuple[int, int] | None:
        """Decode a raw progress line.

        Args:
            line: One output line.

        Returns:
            ``(downloaded, total)`` bytes, or ``None``.

        """
        match: Match[str] | None = ProgressConstants.pip_bar.match(
            string=line.strip(),
        )
        if match is None:
            return None
        return int(match.group("done")), int(match.group("total"))

    @staticmethod
    def artifact(*, line: str) -> str | None:
        """Decode a ``Downloading`` line into a package name.

        Args:
            line: One output line.

        Returns:
            The package name, or ``None``.

        """
        match: Match[str] | None = ProgressConstants.pip_download.match(
            string=line,
        )
        if match is None:
            return None
        filename: str = match.group("ref").rsplit(sep="/", maxsplit=1)[-1]
        return filename.split(sep="-", maxsplit=1)[0] or None


@final
class ProgressTracker:
    """Turn step events and raw step output into :class:`BuildProgress`.

    The tracker is shared by the HTTP handler threads of the job server, so
    every method takes a lock.
    """

    def __init__(
        self,
        *,
        weights: Sequence[int],
        sink: ProgressSink,
    ) -> None:
        """Initialize the tracker.

        Args:
            weights: Relative cost of each planned step; one per step.
            sink: Receives every update.

        """
        self._weights: Final[tuple[int, ...]] = tuple(
            max(1, weight) for weight in weights
        )
        self._sink: Final[Callable[[BuildProgress], None]] = sink
        self._lock: Final[Lock] = Lock()
        self._step: int = 0
        self._message: str = ""
        self._package: str | None = None
        self._downloaded: int = 0
        self._total: int | None = None
        self._detail: str = ""

    def step(self, *, index: int, message: str) -> None:
        """Record that a step started, which completes the previous one.

        Args:
            index: One-based step index; out-of-range values are clamped.
            message: Step text.

        """
        with self._lock:
            self._begin(index=index, message=message)

    def output(self, *, text: str) -> None:
        """Interpret a chunk of the running step's output.

        Args:
            text: Raw output; carriage returns also end a line.

        """
        with self._lock:
            changed: bool = False
            for line in text.replace("\r", "\n").split(sep="\n"):
                marker: tuple[int, str] | None = StepMarker.parse(line=line)
                if marker is not None:
                    self._begin(index=marker[0], message=marker[1])
                    changed = False
                else:
                    changed |= self._line(line=line.rstrip())
            if changed:
                self._publish(complete=False)

    def finish(self) -> None:
        """Record that the whole build completed."""
        with self._lock:
            self._publish(complete=True)

    def _begin(self, *, index: int, message: str) -> None:
        """Switch to a step and publish it; the caller holds the lock.

        Args:
            index: One-based step index; out-of-range values are clamped.
            message: Step text.

        """
        self._step = min(max(index, 1), len(self._weights))
        self._message = message
        self._package = None
        self._downloaded = 0
        self._total = None
        self._detail = ""
        self._publish(complete=False)

    def _line(self, *, line: str) -> bool:
        """Fold one output line into the state.

        Args:
            line: One output line without its terminator.

        Returns:
            Whether the observable state changed.

        """
        if not line:
            return False
        bar: tuple[int, int] | None = PipOutput.bar(line=line)
        if bar is not None:
            self._downloaded, self._total = bar
            return True
        artifact: str | None = PipOutput.artifact(line=line)
        if artifact is not None:
            self._package = artifact
            self._downloaded = 0
            self._total = None
        self._detail = line[: ProgressConstants.detail_limit]
        return True

    def _publish(self, *, complete: bool) -> None:
        """Send the current state to the sink.

        Args:
            complete: Whether the build finished.

        """
        total: int = sum(self._weights)
        finished: int = sum(self._weights[: max(self._step - 1, 0)])
        self._sink(
            BuildProgress(
                percent=100.0 if complete else finished / total * 100.0,
                step=self._step,
                total_steps=len(self._weights),
                message=self._message,
                package=self._package,
                downloaded_bytes=self._downloaded,
                total_bytes=self._total,
                detail=self._detail,
                complete=complete,
            ),
        )


@final
class ProgressDisplay:
    """Render progress on a terminal without garbling ordinary lines.

    Every step change is written once as a permanent line, so logs and
    scrollback keep the history. On a terminal a live bar is additionally
    redrawn in place beneath the permanent lines; elsewhere (pipes, CI logs)
    only the permanent lines are written.
    """

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Initialize the display.

        Args:
            stream: Output stream; standard output when omitted.
            clock: Monotonic clock used to throttle redraws.

        """
        self._stream: Final[TextIO] = (
            stream if stream is not None else sys_stdout
        )
        self._clock: Final[Callable[[], float]] = clock
        self._live: Final[bool] = self._stream.isatty()
        self._lock: Final[Lock] = Lock()
        self._drawn: bool = False
        self._last_draw: float = 0.0
        self._last_step: int = -1
        self._latest: BuildProgress | None = None

    @classmethod
    def summary(cls, *, progress: BuildProgress) -> str:
        """Compose the one-line description of an update.

        Args:
            progress: The update to describe.

        Returns:
            Bar, percentage, step and detail as plain text.

        """
        width: int = ProgressConstants.bar_width
        filled: int = round(number=progress.percent / 100.0 * width)
        bar: str = "#" * filled + "-" * (width - filled)
        text: str = f"[{bar}] {progress.percent:3.0f}%"
        if progress.complete:
            return f"{text} build complete"
        text += (
            f" step {progress.step}/{progress.total_steps} {progress.message}"
        )
        if progress.package is not None and progress.total_bytes:
            text += (
                f" · {progress.package} "
                f"{cls.size(count=progress.downloaded_bytes)}/"
                f"{cls.size(count=progress.total_bytes)}"
            )
        elif progress.detail:
            text += f" · {progress.detail}"
        return text

    @staticmethod
    def step_line(*, progress: BuildProgress) -> str:
        """Compose the permanent line written when a step starts.

        Args:
            progress: The update that started the step.

        Returns:
            Percentage, step position and step text.

        """
        return (
            f"[{progress.percent:3.0f}%] "
            f"step {progress.step}/{progress.total_steps} {progress.message}"
        )

    @staticmethod
    def size(*, count: int) -> str:
        """Format a byte count.

        Args:
            count: Number of bytes.

        Returns:
            Human-readable size.

        """
        value: float = float(count)
        for unit in ("B", "kB", "MB"):
            if value < ProgressConstants.unit_step:
                digits: int = 0 if unit == "B" else 1
                return f"{value:.{digits}f} {unit}"
            value /= ProgressConstants.unit_step
        return f"{value:.1f} GB"

    def line(self, message: str) -> None:
        """Print a permanent line above the live line.

        Args:
            message: Text to print.

        """
        with self._lock:
            self._erase()
            self._emit(text=message)
            if self._latest is not None and not self._latest.complete:
                self._draw()

    def update(self, progress: BuildProgress) -> None:
        """Show the newest progress.

        Args:
            progress: The update to show.

        """
        with self._lock:
            changed: bool = progress.step != self._last_step
            self._latest = progress
            self._last_step = progress.step
            if progress.complete:
                self._erase()
                self._emit(text=self.summary(progress=progress))
                return
            if changed:
                self._erase()
                self._emit(text=self.step_line(progress=progress))
            elif (
                self._clock() - self._last_draw
                < ProgressConstants.refresh_seconds
            ):
                return
            else:
                self._erase()
            self._draw()

    def _draw(self) -> None:
        """Draw the live line, truncated to the terminal width."""
        if not self._live or self._latest is None:
            return
        columns: int = get_terminal_size(fallback=(100, 24)).columns
        text: str = self.summary(progress=self._latest)
        _ = self._stream.write(text[: max(columns - 1, 10)])
        self._flush()
        self._drawn = True
        self._last_draw = self._clock()

    def _erase(self) -> None:
        """Clear the live line when one is on screen."""
        if self._drawn:
            _ = self._stream.write("\r\033[2K")
            self._drawn = False

    def _emit(self, *, text: str) -> None:
        """Write one permanent line.

        Args:
            text: Line without its terminator.

        """
        _ = self._stream.write(f"{text}\n")
        self._flush()

    def _flush(self) -> None:
        """Flush the stream."""
        self._stream.flush()
