# Copyright (c) 2026 Project contributors.
"""Maintain the merged artifact manifest and the generated ``BUILD.md``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from json import JSONDecodeError, dumps, loads
from platform import platform, python_version
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from pathlib import Path

    from tools.release_builder.release_builder import BuildResult, Runner
type Entry = dict[str, str | float]
type Archive = dict[str, str | int]


class ReportConstants:
    """Group report constants.

    Attributes:
        manifest_name: Machine-readable manifest file name.
        report_name: Generated report file name.
        invocation: Default command shown in the rebuild section.
        key_fields: Fields identifying one build in the manifest.

    """

    manifest_name: Final[str] = "artifacts.json"
    report_name: Final[str] = "BUILD.md"
    invocation: Final[str] = (
        "python -m tools.release_builder --project <project> "
        "--manifest <manifest>"
    )
    key_fields: Final[tuple[str, str]] = ("platform", "architecture")


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildSource:
    """Describe what was built and how to rebuild it.

    Attributes:
        name: Project name.
        version: Project version.
        root: Directory to read the git commit from, if known.
        invocation: Command prefix that reproduces a build.

    """

    name: str
    version: str
    root: Path | None = None
    invocation: str = ReportConstants.invocation


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    """Describe where and when a report was produced.

    Attributes:
        commit: Short git commit, or ``unknown``.
        host: Description of the machine that rendered the report.
        generated_at: UTC timestamp text.

    """

    commit: str
    host: str
    generated_at: str


class BuildReport:
    """Merge build results over time and describe them in ``BUILD.md``.

    A partial build (for example only macOS) keeps the other platforms'
    earlier entries, as long as their files still exist, so the report
    always describes everything currently in the output directory.
    """

    @classmethod
    def merge(
        cls,
        *,
        output: Path,
        results: tuple[BuildResult, ...],
        built_at: str,
    ) -> list[Entry]:
        """Combine earlier entries with fresh results.

        Args:
            output: Output root.
            results: Fresh build results.
            built_at: UTC timestamp text for the fresh results.

        Returns:
            Entries sorted by platform and architecture; entries whose files
            vanished or that a fresh result replaces are dropped.

        """
        fresh: list[Entry] = [
            cls._entry(result=result, output=output, built_at=built_at)
            for result in results
        ]
        replaced: set[tuple[str | float | None, ...]] = {
            tuple(entry.get(key) for key in ReportConstants.key_fields)
            for entry in fresh
        }
        kept: list[Entry] = [
            entry
            for entry in cls._previous(output=output)
            if tuple(entry.get(key) for key in ReportConstants.key_fields)
            not in replaced
            and (output / str(object=entry.get("executable", ""))).is_file()
            and (output / str(object=entry.get("installer", ""))).is_file()
        ]
        return sorted(
            [*kept, *fresh],
            key=lambda entry: (
                str(object=entry["platform"]),
                str(object=entry["architecture"]),
            ),
        )

    @classmethod
    def archives(cls, *, output: Path) -> list[Archive]:
        """Describe the ``.pyz`` archives in the output directory.

        Args:
            output: Output root.

        Returns:
            One record per archive, sorted by name.

        """
        return [
            {
                "file": path.name,
                "size": path.stat().st_size,
                "sha256": sha256(data=path.read_bytes()).hexdigest(),
                "built_at": cls._stamp(
                    moment=datetime.fromtimestamp(
                        path.stat().st_mtime,
                        tz=UTC,
                    ),
                ),
            }
            for path in sorted(output.glob(pattern="*.pyz"))
        ]

    @classmethod
    def write(
        cls,
        *,
        output: Path,
        source: BuildSource,
        runner: Runner,
        results: tuple[BuildResult, ...] = (),
    ) -> Path:
        """Update ``artifacts.json`` and regenerate ``BUILD.md``.

        Args:
            output: Output root (created when missing).
            source: What was built and how to rebuild it.
            runner: Command executor, used to read the git commit.
            results: Fresh build results to merge in.

        Returns:
            The path of ``BUILD.md``.

        """
        output.mkdir(parents=True, exist_ok=True)
        now: str = cls._stamp(moment=datetime.now(tz=UTC))
        entries: list[Entry] = cls.merge(
            output=output,
            results=results,
            built_at=now,
        )
        _ = (output / ReportConstants.manifest_name).write_text(
            data=dumps(obj=entries, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report: Path = output / ReportConstants.report_name
        _ = report.write_text(
            data=cls.render(
                source=source,
                provenance=Provenance(
                    commit=cls._commit(
                        runner=runner,
                        project_root=source.root,
                    ),
                    host=f"{platform()} · Python {python_version()}",
                    generated_at=now,
                ),
                entries=entries,
                archives=cls.archives(output=output),
            ),
            encoding="utf-8",
        )
        return report

    @staticmethod
    def render(
        *,
        source: BuildSource,
        provenance: Provenance,
        entries: list[Entry],
        archives: list[Archive],
    ) -> str:
        """Render the report text.

        Args:
            source: What was built and how to rebuild it.
            provenance: Where and when the report was produced.
            entries: Merged manifest entries.
            archives: ``.pyz`` archive records.

        Returns:
            Markdown text.

        """
        lines: list[str] = [
            "# Build",
            "",
            "> Generated by `tools.release_builder` — do not edit by hand. "
            "Run the builder again to refresh it.",
            "",
            "| | |",
            "| --- | --- |",
            f"| Project | {source.name} {source.version} |",
            f"| Commit | `{provenance.commit}` |",
            f"| Generated | {provenance.generated_at} |",
            f"| Generated on | {provenance.host} |",
            "",
            "## Executables and installers",
            "",
        ]
        if entries:
            lines += [
                "| Platform | Architecture | Executable | Installer "
                "| Built | Duration |",
                "| --- | --- | --- | --- | --- | --- |",
                *(
                    f"| {e['platform']} | {e['architecture']} "
                    f"| `{e['executable']}` | `{e['installer']}` "
                    f"| {e.get('built_at', '')} "
                    f"| {e['duration_seconds']} s |"
                    for e in entries
                ),
            ]
        else:
            lines.append("No builds yet.")
        if archives:
            lines += [
                "",
                "## Archives (`.pyz`)",
                "",
                "| File | Size | Built |",
                "| --- | ---: | --- |",
                *(
                    f"| `{a['file']}` | {a['size']} bytes | {a['built_at']} |"
                    for a in archives
                ),
            ]
        checksums: list[str] = [
            *(
                f"{e[key]}  {e[path]}"
                for e in entries
                for key, path in (
                    ("executable_sha256", "executable"),
                    ("installer_sha256", "installer"),
                )
            ),
            *(f"{a['sha256']}  {a['file']}" for a in archives),
        ]
        if checksums:
            lines += [
                "",
                "## SHA-256",
                "",
                "Run from this directory: `shasum -a 256 -c SHA256SUMS`.",
                "",
                "```text",
                *checksums,
                "```",
            ]
        platforms: list[str] = sorted(
            {str(object=e["platform"]) for e in entries},
        )
        if platforms:
            lines += [
                "",
                "## Rebuild",
                "",
                "```bash",
                *(
                    f"{source.invocation} --output build "
                    f"--platform {platform_name}"
                    for platform_name in platforms
                ),
                "```",
            ]
        return "\n".join(lines) + "\n"

    @classmethod
    def _entry(
        cls,
        *,
        result: BuildResult,
        output: Path,
        built_at: str,
    ) -> Entry:
        """Describe one fresh build result.

        Args:
            result: Completed build.
            output: Output root.
            built_at: UTC timestamp text.

        Returns:
            Manifest entry.

        """
        return {
            "platform": result.platform,
            "architecture": result.architecture,
            "executable": cls._relative(path=result.executable, output=output),
            "installer": cls._relative(path=result.installer, output=output),
            "executable_sha256": result.executable_sha256,
            "installer_sha256": result.installer_sha256,
            "duration_seconds": round(
                number=result.duration_seconds,
                ndigits=1,
            ),
            "built_at": built_at,
        }

    @staticmethod
    def _stamp(*, moment: datetime) -> str:
        """Format a UTC moment for the manifest and the report.

        Args:
            moment: Timestamp to format.

        Returns:
            ``YYYY-MM-DDTHH:MM:SSZ`` text.

        """
        return moment.strftime(format="%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _relative(*, path: Path, output: Path) -> str:
        """Return a portable, output-relative path.

        Args:
            path: Artifact path.
            output: Output root.

        Returns:
            POSIX path relative to ``output`` (or the bare name outside it).

        """
        try:
            return path.resolve().relative_to(output.resolve()).as_posix()
        except ValueError:
            return path.name

    @staticmethod
    def _previous(*, output: Path) -> list[Entry]:
        """Load the existing manifest, tolerating absence or damage.

        Args:
            output: Output root.

        Returns:
            Earlier entries.

        """
        path: Path = output / ReportConstants.manifest_name
        if not path.is_file():
            return []
        try:
            document: object = loads(s=path.read_text(encoding="utf-8"))
        except (JSONDecodeError, OSError):
            return []
        if not isinstance(document, list):
            return []
        items: list[object] = cast(typ="list[object]", val=document)
        return [
            cast(typ="Entry", val=item)
            for item in items
            if isinstance(item, dict)
        ]

    @staticmethod
    def _commit(*, runner: Runner, project_root: Path | None) -> str:
        """Return the short git commit of the project, if available.

        Args:
            runner: Command executor.
            project_root: Directory to ask git in.

        Returns:
            The abbreviated commit, or ``unknown``.

        """
        try:
            return (
                runner.execute(
                    arguments=("git", "rev-parse", "--short", "HEAD"),
                    cwd=project_root,
                ).stdout.strip()
                or "unknown"
            )
        except (RuntimeError, OSError):
            return "unknown"
