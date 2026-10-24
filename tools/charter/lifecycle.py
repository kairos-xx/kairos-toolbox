# Copyright (c) 2026 Project contributors.
"""Charter lifetime: keep alive, expire after a time, stop on command.

A scoped charter (the default) ends when its ``with`` block or ``close``
runs. With ``keep_alive`` it runs in its own session, outlives the Python
process, and is listed in a small JSON registry so a later ``charter stop``
can end it. ``ttl_seconds`` ends either kind after a delay: a timer thread
for scoped tunnels, a detached watchdog process for kept-alive ones.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from json import JSONDecodeError, dumps, loads
from os import kill
from os import name as os_name
from pathlib import Path
from signal import SIGTERM, Signals
from subprocess import DEVNULL, Popen
from sys import executable
from tempfile import NamedTemporaryFile
from threading import Timer
from time import monotonic, sleep, time
from typing import TYPE_CHECKING, Final

from tools.charter.errors import CharterError

if TYPE_CHECKING:
    from tools.charter.models import Charter
    from tools.charter.settings import Settings


class LifecycleConstants:
    """Group lifecycle constants.

    Attributes:
        registry: Default registry location.
        poll_seconds: Pause while waiting for a process to exit.
        project: Directory that makes ``tools.charter`` importable.
        force: Signal that cannot be ignored (``SIGKILL`` where it exists).

    """

    registry: Final[Path] = Path.home() / ".charter" / "tunnels.json"
    poll_seconds: Final[float] = 0.05
    project: Final[Path] = Path(__file__).resolve().parents[2]
    force: Final[int] = int(getattr(Signals, "SIGKILL", SIGTERM))


@dataclass(frozen=True, slots=True, kw_only=True)
class CharterRecord:
    """Describe one kept-alive charter.

    Attributes:
        identifier: Registry key (the backend process id as text).
        pid: Backend process id.
        backend: Backend name.
        port: Local port exposed.
        public_url: Public HTTPS URL.
        started_at: Start time as a Unix timestamp.
        expires_at: Expiry as a Unix timestamp, or ``None`` for never.
        log_path: Backend log file, if any.

    """

    identifier: str
    pid: int
    backend: str
    port: int
    public_url: str
    started_at: float
    expires_at: float | None
    log_path: str | None


def require_detach_support() -> None:
    """Refuse keep-alive tunnels where processes cannot be probed safely.

    Raises:
        CharterError: On Windows, where ``os.kill(pid, 0)`` terminates.

    """
    if os_name == "nt":
        msg: str = "keep_alive tunnels need a POSIX host."
        raise CharterError(msg)


def alive(*, pid: int) -> bool:
    """Tell whether a process exists.

    Args:
        pid: Process id.

    Returns:
        ``True`` while the process is running.

    """
    try:
        kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def end_process(*, pid: int, timeout_seconds: float) -> None:
    """Terminate a process, escalating to a kill after a grace period.

    Args:
        pid: Process id.
        timeout_seconds: Seconds to wait after ``SIGTERM`` before ``SIGKILL``.

    """
    try:
        kill(pid, SIGTERM)
    except ProcessLookupError:
        return
    deadline: float = monotonic() + timeout_seconds
    while monotonic() < deadline and alive(pid=pid):
        sleep(LifecycleConstants.poll_seconds)
    if alive(pid=pid):
        try:
            kill(pid, LifecycleConstants.force)
        except ProcessLookupError:
            return


class CharterRegistry:
    """Persist the kept-alive tunnels so they can be listed and stopped."""

    def __init__(self, *, path: Path | None = None) -> None:
        """Initialize the registry.

        Args:
            path: JSON file; ``~/.charter/tunnels.json`` when omitted.

        """
        self._path: Final[Path] = (
            path if path is not None else LifecycleConstants.registry
        )

    @property
    def path(self) -> Path:
        """Return the registry file.

        Returns:
            The JSON file location.

        """
        return self._path

    def add(self, *, record: CharterRecord) -> None:
        """Register a charter.

        Args:
            record: Charter to register; replaces one with the same id.

        """
        records: dict[str, CharterRecord] = self._read()
        records[record.identifier] = record
        self._write(records=records)

    def records(self) -> tuple[CharterRecord, ...]:
        """List live tunnels, forgetting those whose process has ended.

        Returns:
            Records ordered by start time.

        """
        records: dict[str, CharterRecord] = self._read()
        live: dict[str, CharterRecord] = {
            key: record
            for key, record in records.items()
            if alive(pid=record.pid)
        }
        if live != records:
            self._write(records=live)
        return tuple(sorted(live.values(), key=lambda r: r.started_at))

    def stop(
        self,
        *,
        identifier: str,
        timeout_seconds: float,
    ) -> CharterRecord:
        """End a registered charter and forget it.

        Args:
            identifier: Registry key.
            timeout_seconds: Grace period before the process is killed.

        Returns:
            The stopped charter's record.

        Raises:
            CharterError: If no charter has that id.

        """
        records: dict[str, CharterRecord] = self._read()
        if identifier not in records:
            msg: str = f"No kept-alive charter with id {identifier}."
            raise CharterError(msg)
        record: CharterRecord = records.pop(identifier)
        end_process(pid=record.pid, timeout_seconds=timeout_seconds)
        self._write(records=records)
        if record.log_path is not None:
            Path(record.log_path).unlink(missing_ok=True)
        return record

    def stop_pid(self, *, pid: int, timeout_seconds: float) -> None:
        """End a charter process, and forget it if it is registered.

        Args:
            pid: Backend process id.
            timeout_seconds: Grace period before the process is killed.

        """
        known: str = str(object=pid)
        if known in self._read():
            _ = self.stop(identifier=known, timeout_seconds=timeout_seconds)
        else:
            end_process(pid=pid, timeout_seconds=timeout_seconds)

    def _read(self) -> dict[str, CharterRecord]:
        """Load the registry file.

        Returns:
            Records by id; empty when the file is absent or unreadable.

        """
        try:
            document: list[dict[str, str | int | float | None]] = loads(
                s=self._path.read_text(encoding="utf-8"),
            )
        except (OSError, JSONDecodeError):
            return {}
        return {
            str(object=item["identifier"]): CharterRecord(
                identifier=str(object=item["identifier"]),
                pid=int(item["pid"] or 0),
                backend=str(object=item["backend"]),
                port=int(item["port"] or 0),
                public_url=str(object=item["public_url"]),
                started_at=float(item["started_at"] or 0.0),
                expires_at=(
                    float(item["expires_at"])
                    if item["expires_at"] is not None
                    else None
                ),
                log_path=(
                    str(object=item["log_path"])
                    if item["log_path"] is not None
                    else None
                ),
            )
            for item in document
        }

    def _write(self, *, records: dict[str, CharterRecord]) -> None:
        """Replace the registry file atomically.

        Args:
            records: Records to store.

        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._path.parent,
            delete=False,
        ) as stream:
            _ = stream.write(
                dumps(obj=[asdict(obj=r) for r in records.values()], indent=2),
            )
        Path(stream.name).replace(target=self._path)


def schedule_expiry(*, charter: Charter, ttl_seconds: float) -> None:
    """End a charter after a delay.

    A scoped charter is stopped by a daemon timer. A kept-alive charter must
    expire even if this process exits, so a detached watchdog does it.

    Args:
        charter: The running charter.
        ttl_seconds: Seconds until it is stopped.

    """
    if charter.keep_alive:
        _ = Popen(
            args=(
                executable,
                "-m",
                "tools.charter",
                "expire",
                "--pid",
                str(object=charter.process.pid),
                "--seconds",
                str(object=ttl_seconds),
            ),
            cwd=LifecycleConstants.project,
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
            start_new_session=True,
        )
        return
    timer: Timer = Timer(interval=ttl_seconds, function=charter.stop)
    timer.daemon = True
    timer.start()


def manage(
    *,
    charter: Charter,
    backend: str,
    port: int,
    settings: Settings,
) -> None:
    """Apply the configured lifetime to a freshly started charter.

    Args:
        charter: The running charter.
        backend: Backend name.
        port: Local port exposed.
        settings: Resolved settings carrying the lifetime options.

    """
    now: float = time()
    ttl: float | None = settings.ttl_seconds
    if charter.keep_alive:
        registry: CharterRegistry = CharterRegistry(
            path=(
                Path(settings.registry_path)
                if settings.registry_path is not None
                else None
            ),
        )
        registry.add(
            record=CharterRecord(
                identifier=str(object=charter.process.pid),
                pid=charter.process.pid,
                backend=backend,
                port=port,
                public_url=charter.public_url,
                started_at=now,
                expires_at=now + ttl if ttl is not None else None,
                log_path=(
                    str(object=charter.log_path)
                    if charter.log_path is not None
                    else None
                ),
            ),
        )
    if ttl is not None:
        schedule_expiry(charter=charter, ttl_seconds=ttl)
