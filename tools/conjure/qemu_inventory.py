"""QEMU disk-image and running-process discovery."""

from __future__ import annotations

from json import loads
from pathlib import Path
from subprocess import CompletedProcess, run
from typing import Final, cast, final

from tools.conjure.errors import QemuRunnerError
from tools.conjure.inventory import QemuImageInfo, QemuProcessInfo
from tools.conjure.utm import UtmQemuExtractor, UtmVm

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)


@final
class Constants:
    """Group module-level constants.

    Attributes:
        process_fields: Grouped module constant.

    """

    process_fields: Final[int] = 2


class QemuInventory:
    """Discover reusable QEMU resources on the current host."""

    _img_executable: Path
    _process_executable: Path

    def __init__(
        self,
        *,
        img_executable: Path = Path("qemu-img"),
        process_executable: Path = Path("ps"),
    ) -> None:
        """Initialize QEMU discovery executables.

        Args:
            img_executable: Path to the ``qemu-img`` executable.
            process_executable: Path to the ``ps`` executable.

        """
        self._img_executable = img_executable
        self._process_executable = process_executable

    @staticmethod
    def utm_vms(
        *,
        extractor: UtmQemuExtractor | None = None,
    ) -> tuple[UtmVm, ...]:
        """Return the running UTM VMs, when the host is macOS with UTM.

        Args:
            extractor: UTM discovery to use; the host's by default.

        Returns:
            Running UTM-managed QEMU VMs with their captured and standalone
            commands, or nothing on other hosts or without UTM installed.

        """
        active: UtmQemuExtractor = (
            extractor if extractor is not None else UtmQemuExtractor()
        )
        return active.discover() if active.available() else ()

    def images(self, *, roots: tuple[Path, ...]) -> tuple[QemuImageInfo, ...]:
        """Find common QEMU disk images below explicit roots.

        Args:
            roots: Directories to scan for disk images.

        Returns:
            Supported disk images found under the roots.

        """
        suffixes: frozenset[str] = frozenset(
            {".qcow2", ".qcow", ".raw", ".img", ".vhd", ".vhdx", ".vmdk"},
        )
        paths: tuple[Path, ...] = tuple(
            sorted(
                (
                    candidate
                    for root in roots
                    if root.exists()
                    for candidate in root.rglob(pattern="*")
                    if candidate.is_file()
                    and candidate.suffix.lower() in suffixes
                ),
                key=lambda path: str(object=path),
            ),
        )
        return tuple(self.image_details(path=path) for path in paths)

    def image_details(self, *, path: Path) -> QemuImageInfo:
        """Read qemu-img metadata for one disk image.

        Args:
            path: Disk image to inspect.

        Returns:
            Metadata reported by ``qemu-img info``.

        Raises:
            QemuRunnerError: when ``qemu-img info`` fails.

        """
        completed: CompletedProcess[str] = run(
            args=(
                str(object=self._img_executable),
                "info",
                "--output=json",
                str(object=path),
            ),
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise QemuRunnerError(
                completed.stderr.strip() or "qemu-img info failed.",
            )
        decoded: JsonValue = cast(
            typ="JsonValue",
            val=loads(s=completed.stdout),
        )
        if not isinstance(decoded, dict):
            msg: str = "qemu-img returned invalid metadata."
            raise QemuRunnerError(msg)
        metadata: dict[str, JsonValue] = decoded
        return QemuImageInfo(
            path=path,
            format=self._optional_string(value=metadata.get("format")),
            virtual_size_bytes=self._optional_int(
                value=metadata.get("virtual-size"),
            ),
            actual_size_bytes=self._optional_int(
                value=metadata.get("actual-size"),
            ),
        )

    def processes(self) -> tuple[QemuProcessInfo, ...]:
        """Return running QEMU system processes on POSIX hosts.

        Returns:
            Discovered QEMU processes. Windows callers can still attach by
            explicit SSH endpoint; process discovery is intentionally isolated
            from execution and may be replaced by a platform adapter later.

        Raises:
            QemuRunnerError: when process enumeration fails.

        """
        completed: CompletedProcess[str] = run(
            args=(
                str(object=self._process_executable),
                "-axo",
                "pid=,command=",
            ),
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise QemuRunnerError(
                completed.stderr.strip() or "Process discovery failed.",
            )
        found: list[QemuProcessInfo] = []
        for line in completed.stdout.splitlines():
            stripped: str = line.strip()
            if not stripped:
                continue
            fields: list[str] = stripped.split()
            if (
                len(fields) < Constants.process_fields
                or "qemu-system-" not in fields[1]
            ):
                continue
            found.append(
                QemuProcessInfo(
                    process_id=int(fields[0]),
                    executable=Path(fields[1]),
                    arguments=tuple(fields[2:]),
                ),
            )
        return tuple(found)

    @staticmethod
    def _optional_string(*, value: JsonValue | None) -> str | None:
        """Return a string value when precisely typed.

        Args:
            value: Decoded JSON value to coerce.

        Returns:
            The string value, or ``None``.

        """
        return value if isinstance(value, str) else None

    @staticmethod
    def _optional_int(*, value: JsonValue | None) -> int | None:
        """Return an integer value when precisely typed.

        Args:
            value: Decoded JSON value to coerce.

        Returns:
            The integer value, or ``None``.

        """
        return value if isinstance(value, int) else None
