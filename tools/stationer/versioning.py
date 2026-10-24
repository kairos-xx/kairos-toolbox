"""Strict semantic-version operations."""

from __future__ import annotations

from dataclasses import dataclass
from re import Pattern, escape, subn
from re import compile as regex_compile
from typing import Final, final

from tools.stationer.models import Bump


@final
class Constants:
    """Group module-level constants.

    Attributes:
        version: Grouped module constant.

    """

    version: Final[Pattern[str]] = regex_compile(
        pattern="^(?P<major>0|[1-9]\\d*)\\.(?P<minor>0|[1-9]\\d*)\\.(?P<patch>0|[1-9]\\d*)(?:-(?P<prerelease>[0-9A-Za-z.-]+))?(?:\\+(?P<build>[0-9A-Za-z.-]+))?$",
    )


@dataclass(frozen=True, order=True, slots=True, kw_only=True)
class Version:
    """Semantic version.

    Attributes:
        major: Major version component.
        minor: Minor version component.
        patch: Patch version component.
        prerelease: Optional prerelease label.
        build: Optional build metadata.

    """

    major: int
    minor: int
    patch: int
    prerelease: str | None = None
    build: str | None = None

    @classmethod
    def parse(cls, *, value: str) -> Version:
        """Parse a semantic version.

        Args:
            value: Semantic version text such as ``1.2.3``.

        Returns:
            The parsed version.

        Raises:
            ValueError: If ``value`` is not a valid semantic version.

        """
        if (match := Constants.version.fullmatch(string=value)) is None:
            msg: str = f"Invalid semantic version: {value!r}"
            raise ValueError(msg)
        return cls(
            major=int(match["major"]),
            minor=int(match["minor"]),
            patch=int(match["patch"]),
            prerelease=match["prerelease"],
            build=match["build"],
        )

    def bump(self, *, kind: Bump) -> Version:
        """Return the incremented stable version.

        Args:
            kind: Which component to increment.

        Returns:
            The bumped version without prerelease or build metadata.

        """
        if kind is Bump.major:
            return Version(major=self.major + 1, minor=0, patch=0)
        if kind is Bump.minor:
            return Version(major=self.major, minor=self.minor + 1, patch=0)
        return Version(
            major=self.major,
            minor=self.minor,
            patch=self.patch + 1,
        )

    def __str__(self) -> str:
        """Render canonical semantic version text.

        Returns:
            The ``major.minor.patch`` text with optional suffixes.

        """
        suffix: str = (
            f"-{self.prerelease}" if self.prerelease is not None else ""
        )
        build: str = f"+{self.build}" if self.build is not None else ""
        return f"{self.major}.{self.minor}.{self.patch}{suffix}{build}"


_version_line: Final[Pattern[str]] = regex_compile(
    pattern="(?m)^(\\s*version(?:\\s*:[^=\\n]+)?\\s*=\\s*[\"'])([^\"'\\n]+)([\"'])",
)


def read_version(*, text: str) -> str | None:
    """Return the first ``version = "X"`` value in TOML or Python source.

    Both ``version = "1.0.0"`` and annotated assignments such as
    ``version: Final[str] = "1.0.0"`` are recognised.

    Args:
        text: Source text to inspect.

    Returns:
        The version value, or ``None`` when no assignment is found.

    """
    if (match := _version_line.search(string=text)) is None:
        return None
    return match.group(2)


def replace_version(*, text: str, old: str, new: str) -> tuple[str, int]:
    """Rewrite the first version assignment whose value is ``old``.

    Args:
        text: Source text to rewrite.
        old: Existing version value to replace.
        new: Replacement version value.

    Returns:
        The updated text and the number of replacements (0 or 1).

    """
    pattern: Pattern[str] = regex_compile(
        pattern="(?m)^(\\s*version(?:\\s*:[^=\\n]+)?\\s*=\\s*[\"'])"
        + escape(pattern=old)
        + "([\"'])",
    )
    return subn(
        pattern=pattern,
        repl=f"\\g<1>{new}\\g<2>",
        string=text,
        count=1,
    )


type _PrereleaseIdentifierRank = tuple[int, int | str]
type _VersionRank = tuple[
    int,
    int,
    int,
    int,
    tuple[_PrereleaseIdentifierRank, ...],
]


def _rank(*, version: Version) -> _VersionRank:
    """Order key treating a stable release above any prerelease.

    Args:
        version: Parsed semantic version.

    Returns:
        Comparable ordering key.

    """
    identifiers: tuple[_PrereleaseIdentifierRank, ...] = (
        ()
        if version.prerelease is None
        else tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in version.prerelease.split(sep=".")
        )
    )
    return (
        version.major,
        version.minor,
        version.patch,
        int(version.prerelease is None),
        identifiers,
    )


def is_newer(
    *,
    candidate: str,
    current: str,
    allow_prerelease: bool = False,
) -> bool:
    """Whether ``candidate`` is a newer stable release than ``current``.

    Args:
        candidate: Candidate version text.
        current: Currently installed version text.
        allow_prerelease: Consider prerelease candidates newer; stable
            channels leave this off.

    Returns:
        ``True`` when candidate outranks current; malformed input and
        ignored prereleases return ``False``.

    """
    normalized: Pattern[str] = regex_compile(
        pattern="^(\\d+\\.\\d+\\.\\d+)(?=[A-Za-z])",
    )
    try:
        next_version: Version = Version.parse(
            value=subn(
                pattern=normalized,
                repl="\\g<1>-",
                string=candidate,
                count=1,
            )[0],
        )
        have_version: Version = Version.parse(
            value=subn(
                pattern=normalized,
                repl="\\g<1>-",
                string=current,
                count=1,
            )[0],
        )
    except ValueError:
        return False
    if next_version.prerelease is not None and not allow_prerelease:
        return False
    return _rank(version=next_version) > _rank(version=have_version)
